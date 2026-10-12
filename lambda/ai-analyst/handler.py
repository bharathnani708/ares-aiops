"""ARES ai-analyst Lambda: the AIOps reasoning layer.

Input (from Mission Control):
  {"question": str, "history": [{"role","content"}], "snapshot": {...}}
    snapshot = Kubernetes pods/events, Argo CD apps, Prometheus metrics/alerts

This function adds the CloudWatch side of the picture (error logs, trace
spans written by habitat-api, chaos/restart events) using Logs Insights, then
asks Claude on Amazon Bedrock for a structured diagnosis.

Output: {"status", "answer", "root_cause", "next_steps", "evidence", "meta"}
"""

from __future__ import annotations

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor

import boto3
from anthropic import AnthropicBedrockMantle

LOG_GROUP = os.environ["APP_LOG_GROUP"]
MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "anthropic.claude-opus-5-5")
EFFORT = os.getenv("ANALYST_EFFORT", "medium")
WINDOW_MINUTES = int(os.getenv("WINDOW_MINUTES", "15"))
REGION = os.getenv("AWS_REGION", "us-east-1")

log = logging.getLogger()
log.setLevel(logging.INFO)

logs = boto3.client("logs", region_name=REGION)
claude = AnthropicBedrockMantle(aws_region=REGION)

# Logs Insights queries over the Fluent Bit stream. habitat-api writes JSON
# lines; Fluent Bit's kubernetes filter merges them into top-level fields.
QUERIES = {
    "recent_problems": """
        fields @timestamp, level, msg, error, subsystem, status, scenario, trace_id, kubernetes.pod_name as pod
        | filter level in ["ERROR", "CRITICAL", "WARNING"]
        | sort @timestamp desc | limit 40""",
    "level_counts": """
        filter ispresent(level)
        | stats count(*) as lines by level, kubernetes.container_name as container""",
    "span_latency": """
        filter ispresent(span) and ispresent(duration_ms)
        | stats count(*) as spans, avg(duration_ms) as avg_ms, pct(duration_ms, 95) as p95_ms, max(duration_ms) as max_ms by span""",
    "slowest_traces": """
        filter span = "http.request"
        | sort duration_ms desc | limit 5
        | fields @timestamp, trace_id, path, status, duration_ms""",
    "lifecycle_events": """
        filter ispresent(event)
        | sort @timestamp desc | limit 20
        | fields @timestamp, event, scenario, kubernetes.pod_name as pod""",
}

ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["healthy", "degraded", "critical", "unknown"]},
        "answer": {"type": "string"},
        "root_cause": {"type": "string"},
        "next_steps": {"type": "array", "items": {"type": "string"}},
        "evidence": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["status", "answer", "root_cause", "next_steps", "evidence"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """You are the ARES Ops Copilot, a senior site reliability engineer on call for \
ARES-1, a simulated Mars habitat. Its workload is the `habitat-api` service on Amazon EKS, deployed \
by Argo CD from Git, scraped by Prometheus, with logs shipped to CloudWatch by Fluent Bit.

Each request gives you the operator's question plus fresh telemetry:
- `kubernetes`: pods (phase, readiness, restarts, waiting/termination reasons), recent events, Argo CD sync/health.
- `prometheus`: request rate, error ratio, p95 latency, CPU/memory/throttling per pod, O2 and power gauges, firing alerts, active chaos flags.
- `cloudwatch`: Logs Insights results for the last few minutes: warnings/errors, per-span latency (lightweight traces), slowest traces, lifecycle/chaos events.

Answer the question the operator actually asked, in a calm mission-control voice. Ground every claim in \
the telemetry and cite the specific numbers, pods, trace IDs or log lines in `evidence`. When something is \
wrong, connect symptoms to a probable root cause (for example: OOMKilled + rising memory = memory leak; \
5xx spike + RelayTimeout logs = comms dependency failure; slow `subsystem.power-grid` spans = power-grid \
latency) and give concrete, ordered next steps an engineer can run (kubectl, Argo CD, Grafana, or the \
Mission Control "Clear all faults" button for injected chaos). If the data is missing or contradictory, \
say so and set status to "unknown" rather than guessing. Keep `answer` under about 150 words; it may use \
short markdown bullets and **bold**. Use an empty string for root_cause and empty lists when healthy and \
nothing applies."""


def _run_query(name: str, query: str) -> tuple[str, object]:
    end = int(time.time())
    try:
        qid = logs.start_query(logGroupName=LOG_GROUP, startTime=end - WINDOW_MINUTES * 60,
                               endTime=end, queryString=query, limit=50)["queryId"]
        deadline = time.time() + 20
        while time.time() < deadline:
            res = logs.get_query_results(queryId=qid)
            if res["status"] in ("Complete", "Failed", "Cancelled", "Timeout"):
                rows = [{f["field"]: f["value"] for f in row if f["field"] != "@ptr"} for row in res["results"]]
                return name, rows if res["status"] == "Complete" else f"query {res['status']}"
            time.sleep(0.7)
        return name, "query timed out"
    except Exception as exc:  # keep reasoning even if one query fails
        log.warning("query %s failed: %s", name, exc)
        return name, f"unavailable: {exc}"


def cloudwatch_context() -> dict:
    with ThreadPoolExecutor(max_workers=len(QUERIES)) as pool:
        return dict(pool.map(lambda item: _run_query(*item), QUERIES.items()))


def analyse(question: str, history: list[dict], snapshot: dict, cw: dict) -> dict:
    telemetry = json.dumps({"window_minutes": WINDOW_MINUTES, **snapshot, "cloudwatch": cw}, default=str)
    messages = [m for m in history if m.get("role") in ("user", "assistant") and m.get("content")]
    # The Messages API needs strictly alternating turns that start with "user".
    while messages and messages[0]["role"] != "user":
        messages.pop(0)
    if messages and messages[-1]["role"] == "user":
        messages.pop()
    messages.append({
        "role": "user",
        "content": f"<telemetry>\n{telemetry}\n</telemetry>\n\nOperator question: {question}",
    })
    response = claude.messages.create(
        model=MODEL_ID,
        max_tokens=8000,
        system=SYSTEM_PROMPT,
        messages=messages,
        output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": ANALYSIS_SCHEMA}},
    )
    usage = {"input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens}
    log.info(json.dumps({"event": "analysis", "model": MODEL_ID, "stop_reason": response.stop_reason, **usage}))
    if response.stop_reason == "refusal":
        return {"status": "unknown", "answer": "The model declined to analyse this request.",
                "root_cause": "", "next_steps": [], "evidence": [], "meta": usage}
    text = next((b.text for b in response.content if b.type == "text"), "{}")
    result = json.loads(text)
    result["meta"] = {**usage, "model": MODEL_ID, "stop_reason": response.stop_reason}
    return result


def lambda_handler(event, context):
    question = (event.get("question") or "What's the status of my application?").strip()[:2000]
    history = event.get("history") or []
    snapshot = event.get("snapshot") or {}
    started = time.time()
    cw = cloudwatch_context()
    result = analyse(question, history, snapshot, cw)
    result.setdefault("meta", {})["latency_s"] = round(time.time() - started, 2)
    return result
