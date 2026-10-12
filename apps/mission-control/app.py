"""ARES Mission Control: the chat-driven AIOps console.

It runs inside the cluster next to habitat-api and:
  * collects a live snapshot (Kubernetes pods/events, Argo CD sync state,
    Prometheus metrics and firing alerts),
  * sends that snapshot plus the operator's question to the ai-analyst Lambda,
    which adds CloudWatch logs/trace spans and asks Claude on Amazon Bedrock,
  * lets the operator inject and clear chaos scenarios on habitat-api.

AWS credentials come from EKS Pod Identity; the only AWS permission this pod
holds is lambda:InvokeFunction on the analyst function.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

import boto3
import httpx
from botocore.config import Config
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

NAMESPACE = os.getenv("APP_NAMESPACE", "ares")
ARGOCD_NAMESPACE = os.getenv("ARGOCD_NAMESPACE", "argocd")
HABITAT_URL = os.getenv("HABITAT_URL", "http://habitat-api.ares.svc.cluster.local")
PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://kube-prometheus-stack-prometheus.monitoring.svc:9090")
ANALYST_FUNCTION = os.getenv("ANALYST_FUNCTION_NAME", "ares-aiops-ai-analyst")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
CHAOS_TOKEN = os.getenv("CHAOS_TOKEN", "")
STATIC_DIR = Path(__file__).parent / "static"

K8S_API = "https://kubernetes.default.svc"
SA_DIR = Path("/var/run/secrets/kubernetes.io/serviceaccount")

logging.basicConfig(stream=sys.stdout, level=logging.INFO,
                    format='{"level":"%(levelname)s","service":"mission-control","msg":"%(message)s"}')
log = logging.getLogger("mission-control")

PROM_QUERIES = {
    "request_rate_rps": 'sum(rate(habitat_http_requests_total{path="/api/telemetry"}[2m]))',
    "error_ratio": 'sum(rate(habitat_http_requests_total{path="/api/telemetry",status=~"5.."}[2m]))'
                   ' / clamp_min(sum(rate(habitat_http_requests_total{path="/api/telemetry"}[2m])), 1e-9)',
    "p95_latency_seconds": 'histogram_quantile(0.95, sum(rate(habitat_http_request_duration_seconds_bucket{path="/api/telemetry"}[2m])) by (le))',
    "oxygen_percent": "avg(habitat_oxygen_percent)",
    "power_kw": "avg(habitat_power_kw)",
    "container_restarts_15m": f'sum(increase(kube_pod_container_status_restarts_total{{namespace="{NAMESPACE}"}}[15m])) by (pod)',
    "memory_working_set_mib": f'sum(container_memory_working_set_bytes{{namespace="{NAMESPACE}",container!=""}}) by (pod) / 1048576',
    "cpu_cores": f'sum(rate(container_cpu_usage_seconds_total{{namespace="{NAMESPACE}",container!=""}}[2m])) by (pod)',
    "cpu_throttled_ratio": f'sum(rate(container_cpu_cfs_throttled_periods_total{{namespace="{NAMESPACE}"}}[2m])) by (pod)'
                           f' / clamp_min(sum(rate(container_cpu_cfs_periods_total{{namespace="{NAMESPACE}"}}[2m])) by (pod), 1e-9)',
    "active_chaos": "max(habitat_chaos_active) by (scenario) > 0",
    "firing_alerts": 'ALERTS{alertstate="firing"}',
}

_http: httpx.AsyncClient | None = None
_k8s: httpx.AsyncClient | None = None
_lambda = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global _http, _k8s, _lambda
    _http = httpx.AsyncClient(timeout=5)
    if (SA_DIR / "token").exists():
        _k8s = httpx.AsyncClient(base_url=K8S_API, verify=str(SA_DIR / "ca.crt"), timeout=5)
    _lambda = boto3.client("lambda", region_name=AWS_REGION,
                           config=Config(read_timeout=150, retries={"max_attempts": 1}))
    yield
    await _http.aclose()
    if _k8s:
        await _k8s.aclose()


app = FastAPI(title="ARES Mission Control", lifespan=lifespan)


# --------------------------------------------------------------------------- snapshot
async def _k8s_get(path: str) -> dict:
    if _k8s is None:
        return {}
    token = (SA_DIR / "token").read_text()  # re-read: projected tokens rotate
    r = await _k8s.get(path, headers={"Authorization": f"Bearer {token}"})
    r.raise_for_status()
    return r.json()


async def kubernetes_state() -> dict:
    out: dict = {"pods": [], "recent_events": [], "argocd_apps": []}
    try:
        pods = await _k8s_get(f"/api/v1/namespaces/{NAMESPACE}/pods")
        for p in pods.get("items", []):
            statuses = p.get("status", {}).get("containerStatuses", []) or []
            out["pods"].append({
                "name": p["metadata"]["name"],
                "phase": p.get("status", {}).get("phase"),
                "ready": all(s.get("ready") for s in statuses) if statuses else False,
                "restarts": sum(s.get("restartCount", 0) for s in statuses),
                "waiting_reason": next((s["state"]["waiting"].get("reason") for s in statuses
                                        if "waiting" in s.get("state", {})), None),
                "last_termination": next((s["lastState"]["terminated"].get("reason") for s in statuses
                                          if "terminated" in s.get("lastState", {})), None),
                "image": statuses[0].get("image") if statuses else None,
            })
        events = await _k8s_get(f"/api/v1/namespaces/{NAMESPACE}/events")
        items = sorted(events.get("items", []),
                       key=lambda e: e.get("lastTimestamp") or e.get("eventTime") or "", reverse=True)
        out["recent_events"] = [{
            "type": e.get("type"), "reason": e.get("reason"),
            "object": e.get("involvedObject", {}).get("name"),
            "message": (e.get("message") or "")[:240], "count": e.get("count"),
            "last_seen": e.get("lastTimestamp") or e.get("eventTime"),
        } for e in items[:15]]
        apps = await _k8s_get(f"/apis/argoproj.io/v1alpha1/namespaces/{ARGOCD_NAMESPACE}/applications")
        out["argocd_apps"] = [{
            "name": a["metadata"]["name"],
            "sync": a.get("status", {}).get("sync", {}).get("status"),
            "health": a.get("status", {}).get("health", {}).get("status"),
            "revision": (a.get("status", {}).get("sync", {}).get("revision") or "")[:8],
        } for a in apps.get("items", [])]
    except Exception as exc:  # the console should still answer when the API is unreachable
        out["error"] = f"kubernetes API: {exc}"
    return out


async def prometheus_state() -> dict:
    out: dict = {}
    for key, query in PROM_QUERIES.items():
        try:
            r = await _http.get(f"{PROMETHEUS_URL}/api/v1/query", params={"query": query})
            r.raise_for_status()
            result = r.json()["data"]["result"]
            out[key] = [{"labels": {k: v for k, v in s["metric"].items() if k != "__name__"},
                         "value": round(float(s["value"][1]), 4)} for s in result][:20]
        except Exception as exc:
            out[key] = f"unavailable: {exc}"
    return out


async def snapshot() -> dict:
    return {
        "collected_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "namespace": NAMESPACE,
        "kubernetes": await kubernetes_state(),
        "prometheus": await prometheus_state(),
    }


# --------------------------------------------------------------------------- API
class Ask(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    history: list[dict] = Field(default_factory=list, max_length=20)


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/api/status")
async def status():
    return await snapshot()


@app.post("/api/ask")
async def ask(body: Ask):
    snap = await snapshot()
    payload = {
        "question": body.question,
        # Only plain text turns are forwarded; the UI keeps the history.
        "history": [{"role": h.get("role"), "content": str(h.get("content", ""))[:4000]}
                    for h in body.history if h.get("role") in {"user", "assistant"}][-10:],
        "snapshot": snap,
    }
    try:
        resp = _lambda.invoke(FunctionName=ANALYST_FUNCTION, Payload=json.dumps(payload).encode())
        result = json.loads(resp["Payload"].read())
    except Exception as exc:
        log.error(f"analyst invoke failed: {exc}")
        raise HTTPException(status_code=502, detail=f"AI analyst unavailable: {exc}") from exc
    if resp.get("FunctionError"):
        raise HTTPException(status_code=502, detail=result.get("errorMessage", "analyst error"))
    return {"analysis": result, "snapshot": snap}


@app.get("/api/chaos")
async def chaos_list():
    r = await _http.get(f"{HABITAT_URL}/chaos", headers={"x-chaos-token": CHAOS_TOKEN})
    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code, detail=r.text)
    return r.json()


@app.post("/api/chaos/{scenario}")
async def chaos(scenario: str):
    try:
        r = await _http.post(f"{HABITAT_URL}/chaos/{scenario}", headers={"x-chaos-token": CHAOS_TOKEN})
    except httpx.HTTPError as exc:  # reactor-scram kills the pod mid-request
        return {"scenario": scenario, "note": f"habitat-api dropped the connection ({exc.__class__.__name__})"}
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text)
    log.info(f"chaos {scenario} sent")
    return r.json()
