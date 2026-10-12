import pytest
from fastapi.testclient import TestClient

import app as app_module


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(app_module, "CHAOS_TOKEN", "test-token")
    for name in app_module.chaos:
        app_module.chaos[name] = False
    with TestClient(app_module.app) as c:
        yield c, app_module


def test_telemetry_nominal(client):
    c, _ = client
    r = c.get("/api/telemetry")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in {"NOMINAL", "WARNING", "CRITICAL"}
    assert "x-trace-id" in r.headers


def test_metrics_exposed(client):
    c, _ = client
    c.get("/api/telemetry")
    text = c.get("/metrics").text
    assert "habitat_http_requests_total" in text
    assert "habitat_oxygen_percent" in text


def test_chaos_requires_token(client):
    c, _ = client
    assert c.post("/chaos/o2-leak").status_code == 403
    assert c.post("/chaos/o2-leak", headers={"x-chaos-token": "wrong"}).status_code == 403


def test_chaos_toggle_and_reset(client):
    c, mod = client
    h = {"x-chaos-token": "test-token"}
    assert c.post("/chaos/o2-leak", headers=h).json()["active"] == ["o2-leak"]
    assert c.post("/chaos/reset", headers=h).json()["active"] == []
    assert c.post("/chaos/not-a-thing", headers=h).status_code == 404


def test_comms_blackout_returns_503s(client):
    c, mod = client
    mod.chaos["comms-blackout"] = True
    codes = {c.get("/api/telemetry").status_code for _ in range(40)}
    mod.chaos["comms-blackout"] = False
    assert 503 in codes
