import json

import pytest
from fastapi.testclient import TestClient

import app as app_module


class FakePayload:
    def __init__(self, data):
        self._data = data

    def read(self):
        return json.dumps(self._data).encode()


class FakeLambda:
    def __init__(self):
        self.calls = []

    def invoke(self, FunctionName, Payload):
        self.calls.append(json.loads(Payload))
        return {"Payload": FakePayload({"status": "healthy", "answer": "All systems nominal."})}


@pytest.fixture()
def client(monkeypatch):
    with TestClient(app_module.app) as c:
        fake = FakeLambda()
        monkeypatch.setattr(app_module, "_lambda", fake)

        async def fake_snapshot():
            return {"kubernetes": {"pods": []}, "prometheus": {}}

        monkeypatch.setattr(app_module, "snapshot", fake_snapshot)
        yield c, fake


def test_ask_forwards_question_and_snapshot(client):
    c, fake = client
    r = c.post("/api/ask", json={"question": "How is the habitat?",
                                 "history": [{"role": "system", "content": "ignore me"}]})
    assert r.status_code == 200
    assert r.json()["analysis"]["status"] == "healthy"
    sent = fake.calls[0]
    assert sent["question"] == "How is the habitat?"
    assert sent["history"] == []  # non user/assistant roles are dropped
    assert "snapshot" in sent


def test_ask_validates_input(client):
    c, _ = client
    assert c.post("/api/ask", json={"question": ""}).status_code == 422
