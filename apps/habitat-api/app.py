"""ARES habitat-api: life-support telemetry for a (simulated) Mars habitat.

It is the workload the AIOps stack watches. It exposes:
  /                 the habitat dashboard (static page)
  /api/telemetry    live readings from the simulated subsystems
  /metrics          Prometheus metrics
  /healthz /readyz  probes
  /chaos/*          token-protected fault injection used by Mission Control

Every request writes one JSON log line per span, sharing a trace_id, so the
AI analyst can reason over logs *and* lightweight traces from CloudWatch.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import random
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager
from contextvars import ContextVar
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

SERVICE = "habitat-api"
VERSION = os.getenv("APP_VERSION", "dev")
CHAOS_TOKEN = os.getenv("CHAOS_TOKEN", "")
STATIC_DIR = Path(__file__).parent / "static"

trace_id_var: ContextVar[str] = ContextVar("trace_id", default="-")


# --------------------------------------------------------------------------- logging
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "service": SERVICE,
            "version": VERSION,
            "trace_id": trace_id_var.get(),
            "msg": record.getMessage(),
        }
        payload.update(getattr(record, "fields", {}))
        return json.dumps(payload)


handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(JsonFormatter())
log = logging.getLogger(SERVICE)
log.handlers = [handler]
log.setLevel(logging.INFO)
log.propagate = False


def emit(level: int, msg: str, **fields) -> None:
    log.log(level, msg, extra={"fields": fields})


# --------------------------------------------------------------------------- metrics
REQUESTS = Counter("habitat_http_requests_total", "HTTP requests", ["path", "method", "status"])
LATENCY = Histogram(
    "habitat_http_request_duration_seconds", "HTTP request latency", ["path"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 4, 8),
)
SPAN_LATENCY = Histogram("habitat_subsystem_span_seconds", "Subsystem span latency", ["subsystem"])
OXYGEN = Gauge("habitat_oxygen_percent", "Cabin O2 concentration (%)")
POWER = Gauge("habitat_power_kw", "Solar array output (kW)")
WATER = Gauge("habitat_water_liters", "Water reserve (liters)")
CREW = Gauge("habitat_crew_count", "Crew on board")
CHAOS_ACTIVE = Gauge("habitat_chaos_active", "Fault injection active (1=yes)", ["scenario"])


# --------------------------------------------------------------------------- chaos state
SCENARIOS = {
    "dust-storm": "Dust storm: solar output drops and every request gets 1.5-3s slower",
    "comms-blackout": "Comms blackout: ~40% of requests fail with HTTP 503",
    "o2-leak": "O2 leak: cabin oxygen falls steadily and critical alerts fire",
    "memory-leak": "Hydroponics memory leak: the pod grows ~20 MiB/s until it is OOMKilled",
    "thermal-runaway": "Thermal runaway: a CPU-burning loop throttles the container",
    "reactor-scram": "Reactor SCRAM: the process exits immediately (pod restarts)",
}
chaos: dict[str, bool] = {name: False for name in SCENARIOS}
_leak: list[bytearray] = []
_state_lock = threading.Lock()
state = {"oxygen": 20.9, "power": 42.0, "water": 1800.0, "crew": 6}


def _simulator() -> None:
    """Background loop: drift the habitat readings and apply active faults."""
    while True:
        with _state_lock:
            if chaos["o2-leak"]:
                state["oxygen"] = max(14.0, state["oxygen"] - random.uniform(0.15, 0.35))
                if state["oxygen"] < 19.5:
                    emit(logging.WARNING, "O2 concentration below safe threshold",
                         subsystem="life-support", oxygen_percent=round(state["oxygen"], 2))
            else:
                state["oxygen"] += (20.9 - state["oxygen"]) * 0.1 + random.uniform(-0.03, 0.03)
            target_power = 9.0 if chaos["dust-storm"] else 42.0
            state["power"] += (target_power - state["power"]) * 0.2 + random.uniform(-0.5, 0.5)
            state["water"] = max(0.0, state["water"] - random.uniform(0.0, 0.2))
            OXYGEN.set(round(state["oxygen"], 2))
            POWER.set(round(state["power"], 2))
            WATER.set(round(state["water"], 1))
            CREW.set(state["crew"])
        if chaos["memory-leak"]:
            _leak.append(bytearray(20 * 1024 * 1024))
            emit(logging.WARNING, "hydroponics buffer growing", subsystem="hydroponics",
                 leaked_mib=len(_leak) * 20)
        for name, on in chaos.items():
            CHAOS_ACTIVE.labels(scenario=name).set(1 if on else 0)
        time.sleep(1)


def _burn_cpu() -> None:
    while chaos["thermal-runaway"]:
        sum(i * i for i in range(50_000))


# --------------------------------------------------------------------------- app
@asynccontextmanager
async def lifespan(_: FastAPI):
    threading.Thread(target=_simulator, daemon=True, name="simulator").start()
    emit(logging.INFO, "habitat-api online", event="startup")
    yield


app = FastAPI(title="ARES habitat-api", version=VERSION, lifespan=lifespan)


@app.middleware("http")
async def trace_and_measure(request: Request, call_next):
    trace_id = request.headers.get("x-trace-id") or uuid.uuid4().hex[:16]
    trace_id_var.set(trace_id)
    path = request.url.path
    route = path if path in {"/", "/api/telemetry", "/metrics", "/healthz", "/readyz"} else (
        "/chaos" if path.startswith("/chaos") else "other")
    start = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
    finally:
        elapsed = time.perf_counter() - start
        REQUESTS.labels(route, request.method, str(status)).inc()
        LATENCY.labels(route).observe(elapsed)
        if route not in {"/metrics", "/healthz", "/readyz"}:
            emit(logging.ERROR if status >= 500 else logging.INFO, "request",
                 span="http.request", path=route, method=request.method,
                 status=status, duration_ms=round(elapsed * 1000, 1))
    response.headers["x-trace-id"] = trace_id
    return response


def _span(subsystem: str, base_ms: float) -> float:
    """Simulate a call into a habitat subsystem and log it as a child span."""
    delay = base_ms / 1000 + random.uniform(0, 0.01)
    if chaos["dust-storm"] and subsystem == "power-grid":
        delay += random.uniform(1.5, 3.0)
    time.sleep(delay)
    SPAN_LATENCY.labels(subsystem).observe(delay)
    emit(logging.INFO, "span", span=f"subsystem.{subsystem}", duration_ms=round(delay * 1000, 1))
    return delay


@app.get("/api/telemetry")
def telemetry():
    if chaos["comms-blackout"] and random.random() < 0.4:
        emit(logging.ERROR, "uplink to orbital relay timed out", subsystem="comms",
             error="RelayTimeout", relay="phobos-relay-2")
        raise HTTPException(status_code=503, detail="Comms blackout: orbital relay unreachable")
    for subsystem, base in (("life-support", 8), ("power-grid", 12), ("water-recycler", 6)):
        _span(subsystem, base)
    with _state_lock:
        snapshot = dict(state)
    o2 = snapshot["oxygen"]
    return {
        "habitat": "ARES-1, Jezero Crater",
        "sol": int(time.time() // 88775) - 19000,
        "oxygen_percent": round(o2, 2),
        "power_kw": round(snapshot["power"], 1),
        "water_liters": round(snapshot["water"], 1),
        "crew": snapshot["crew"],
        "status": "CRITICAL" if o2 < 18 else ("WARNING" if o2 < 19.5 or snapshot["power"] < 15 else "NOMINAL"),
        "active_faults": [name for name, on in chaos.items() if on],
        "version": VERSION,
    }


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/readyz")
def readyz():
    return {"ready": True}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# --------------------------------------------------------------------------- chaos API
def _authorize(token: str | None) -> None:
    # Fail closed: with no token configured, chaos is disabled entirely.
    if not CHAOS_TOKEN or not token or not hmac.compare_digest(token, CHAOS_TOKEN):
        raise HTTPException(status_code=403, detail="chaos access denied")


@app.get("/chaos")
def chaos_list(x_chaos_token: str | None = Header(default=None)):
    _authorize(x_chaos_token)
    return {"scenarios": SCENARIOS, "active": [n for n, on in chaos.items() if on]}


@app.post("/chaos/{scenario}")
def chaos_start(scenario: str, x_chaos_token: str | None = Header(default=None)):
    _authorize(x_chaos_token)
    if scenario == "reset":
        for name in chaos:
            chaos[name] = False
        _leak.clear()
        emit(logging.INFO, "all faults cleared", event="chaos.reset")
        return {"active": []}
    if scenario not in SCENARIOS:
        raise HTTPException(status_code=404, detail="unknown scenario")
    emit(logging.WARNING, "fault injected", event="chaos.start", scenario=scenario)
    if scenario == "reactor-scram":
        emit(logging.CRITICAL, "REACTOR SCRAM - emergency shutdown", subsystem="reactor")
        threading.Timer(0.5, lambda: os._exit(1)).start()
        return JSONResponse({"scenario": scenario, "note": "process exiting"}, status_code=202)
    chaos[scenario] = True
    if scenario == "thermal-runaway":
        threading.Thread(target=_burn_cpu, daemon=True).start()
    return {"active": [n for n, on in chaos.items() if on]}
