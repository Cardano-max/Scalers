"""FastAPI surface tests (HARN-01): /healthz + demo run endpoint."""

from __future__ import annotations

from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def test_healthz_reports_pins_and_temperature():
    resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["temperature"] == 0.0
    assert body["models"]["opus"] == "claude-opus-4-8"
    assert body["models"]["sonnet"] == "claude-sonnet-4-6"
    assert body["models"]["haiku"] == "claude-haiku-4-5"
    assert body["checkpointer"] == "memory"


def test_demo_run_research_to_assemble_and_routes():
    resp = client.post("/runs", json={"topic": "launch", "thread_id": "demo-1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["topic"] == "launch"
    assert "launch" in body["draft"]
    assert body["step_log"] == ["research", "assemble"]
    assert 0.0 <= body["confidence"] <= 1.0
    # Deterministic confidence (3 findings -> 0.9) clears the auto bar.
    assert body["decision"] == "auto"


def test_demo_run_is_deterministic():
    a = client.post("/runs", json={"topic": "same", "thread_id": "x"}).json()
    b = client.post("/runs", json={"topic": "same", "thread_id": "y"}).json()
    assert a["draft"] == b["draft"]
    assert a["confidence"] == b["confidence"]
    assert a["decision"] == b["decision"]


def test_review_autonomy_forces_review():
    resp = client.post(
        "/runs",
        json={"topic": "x", "thread_id": "rev", "autonomy": "review"},
    )
    assert resp.json()["decision"] == "review"


def test_run_requires_topic_and_thread_id():
    assert client.post("/runs", json={"thread_id": "t"}).status_code == 422
    assert client.post("/runs", json={"topic": "t"}).status_code == 422
