"""Thin-portal tests (HARN-01): /healthz + webhook ingress + SSE out.

FastAPI is the thin ingress/egress, not the engine. These tests assert it
forwards LangGraph frames and acknowledges webhooks — they do not test control
logic in the portal (there is none; the graph owns flow).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _force_in_memory(monkeypatch):
    """Keep the portal tests hermetic on the in-memory checkpointer.

    Unsets any ambient ``ENGINE_DATABASE_URL`` (e.g. when the Postgres
    integration suite is run in the same session) and resets the settings +
    cached graph so these tests always exercise the in-memory portal. The real
    Postgres path is covered by ``test_postgres_integration``.
    """

    monkeypatch.delenv("ENGINE_DATABASE_URL", raising=False)
    import harness.graph as graph_mod
    from harness.config import get_settings

    get_settings.cache_clear()
    graph_mod._graph = None
    yield
    get_settings.cache_clear()
    graph_mod._graph = None


def test_healthz_reports_pins_and_temperature():
    resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["temperature"] == 0.0
    assert body["models"]["opus"] == "claude-sonnet-4-5"  # 8sk: top tier == ceiling
    assert body["models"]["sonnet"] == "claude-sonnet-4-5"  # 8sk ceiling
    assert body["models"]["haiku"] == "claude-haiku-4-5"
    assert body["checkpointer"] == "memory"


def test_webhook_ingress_acknowledges_without_running_engine():
    resp = client.post(
        "/webhooks/meta", json={"topic": "x", "thread_id": "wh-1"}
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body == {"run_id": "wh-1", "source": "meta", "status": "accepted"}


def test_webhook_requires_topic_and_thread_id():
    assert client.post("/webhooks/meta", json={"thread_id": "t"}).status_code == 422
    assert client.post("/webhooks/meta", json={"topic": "t"}).status_code == 422


def test_sse_stream_relays_graph_frames_then_decision():
    resp = client.get("/runs/stream", params={"topic": "launch", "thread_id": "sse-1"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    body = resp.text

    # One node frame per node, in fixed order, then the routed decision + DONE.
    assert "event: node" in body
    assert '"node": "research"' in body
    assert '"node": "assemble"' in body
    assert "event: decision" in body
    assert '"decision": "auto"' in body  # 3 findings -> 0.9 clears the auto bar
    assert '"draft"' in body and "launch" in body
    assert "data: [DONE]" in body


def test_sse_stream_is_deterministic():
    a = client.get("/runs/stream", params={"topic": "same", "thread_id": "a"}).text
    b = client.get("/runs/stream", params={"topic": "same", "thread_id": "b"}).text
    # Strip the thread_id-specific run_id from the decision frame before comparing.
    assert a.replace('"a"', '"X"') == b.replace('"b"', '"X"')


def test_sse_review_autonomy_forces_review():
    resp = client.get(
        "/runs/stream",
        params={"topic": "x", "thread_id": "rev", "autonomy": "review"},
    )
    assert '"decision": "review"' in resp.text
