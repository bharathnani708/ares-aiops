import json
import os
import sys
import types

os.environ.setdefault("APP_LOG_GROUP", "/test/group")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

import handler  # noqa: E402


class FakeBlock:
    type = "text"

    def __init__(self, text):
        self.text = text


class FakeMessages:
    def __init__(self):
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        body = {"status": "degraded", "answer": "Latency is high.", "root_cause": "dust storm",
                "next_steps": ["Clear faults"], "evidence": ["p95 2.4s"]}
        return types.SimpleNamespace(
            content=[FakeBlock(json.dumps(body))], stop_reason="end_turn",
            usage=types.SimpleNamespace(input_tokens=10, output_tokens=5))


def test_handler_combines_snapshot_logs_and_model(monkeypatch):
    fake = FakeMessages()
    monkeypatch.setattr(handler, "claude", types.SimpleNamespace(messages=fake))
    monkeypatch.setattr(handler, "cloudwatch_context", lambda: {"recent_problems": [{"msg": "slow"}]})
    out = handler.lambda_handler({
        "question": "why slow?",
        "history": [{"role": "assistant", "content": "hi"}, {"role": "user", "content": "status?"},
                    {"role": "assistant", "content": "fine"}],
        "snapshot": {"prometheus": {"p95_latency_seconds": [{"value": 2.4}]}},
    }, None)
    assert out["status"] == "degraded"
    msgs = fake.kwargs["messages"]
    assert msgs[0]["role"] == "user" and msgs[-1]["role"] == "user"
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
    assert "slow" in msgs[-1]["content"] and "why slow?" in msgs[-1]["content"]
    assert fake.kwargs["output_config"]["format"]["type"] == "json_schema"


def test_falls_back_to_next_model(monkeypatch):
    import anthropic

    calls = []

    class Missing(FakeMessages):
        def create(self, **kwargs):
            calls.append(kwargs["model"])
            if kwargs["model"] == "anthropic.claude-missing":
                resp = types.SimpleNamespace(status_code=404, headers={}, request=None)
                raise anthropic.NotFoundError("not found", response=resp, body=None)
            return super().create(**kwargs)

    fake = Missing()
    monkeypatch.setattr(handler, "claude", types.SimpleNamespace(messages=fake))
    monkeypatch.setattr(handler, "MODEL_IDS", ["anthropic.claude-missing", "anthropic.claude-ok"])
    monkeypatch.setattr(handler, "_working", None)
    monkeypatch.setattr(handler, "cloudwatch_context", lambda: {})
    out = handler.lambda_handler({"question": "status?"}, None)
    assert calls == ["anthropic.claude-missing", "anthropic.claude-ok"]
    assert out["meta"]["model"] == "anthropic.claude-ok"
    assert handler._working == ("mantle", "anthropic.claude-ok")


def test_runtime_prefix_routes_to_runtime_client(monkeypatch):
    fake = FakeMessages()
    monkeypatch.setattr(handler, "claude_runtime", types.SimpleNamespace(messages=fake))
    monkeypatch.setattr(handler, "MODEL_IDS", ["runtime:us.anthropic.claude-sonnet-4-6"])
    monkeypatch.setattr(handler, "_working", None)
    monkeypatch.setattr(handler, "cloudwatch_context", lambda: {})
    out = handler.lambda_handler({"question": "status?"}, None)
    assert fake.kwargs["model"] == "us.anthropic.claude-sonnet-4-6"
    assert out["meta"]["endpoint"] == "runtime"
