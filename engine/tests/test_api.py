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
    # 4jx.13: the demo tenant is HELD by the fail-safe default registry, so the
    # server-side resolution forces REVIEW even though confidence clears the bar.
    assert '"decision": "review"' in body
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


# ── 4jx.13: autonomy is resolved server-side via HoldRegistry ────────────────


def test_held_tenant_can_never_emit_auto_even_requesting_auto():
    """AC 2: with the default (held) registry, an explicit client autonomy=auto
    is powerless — the decision frame is REVIEW and no auto frame/record exists,
    at the demo path's maximum confidence."""
    resp = client.get(
        "/runs/stream",
        params={"topic": "x", "thread_id": "held-auto", "autonomy": "auto"},
    )
    assert '"decision": "review"' in resp.text
    assert '"decision": "auto"' not in resp.text


def test_lifted_tenant_with_auto_dial_still_autos():
    """AC 3 regression: an operator-lifted tenant with the AUTO dial keeps its
    auto path (the resolution only reduces, it does not break lifting)."""
    import asyncio

    from harness.hold import HoldRegistry
    from harness.state import AutonomyMode
    from main import _run_event_stream

    async def collect():
        return "".join([
            f async for f in _run_event_stream(
                "x", "lifted-1", "demo", AutonomyMode.AUTO,
                hold_registry=HoldRegistry().lift("demo"),
            )
        ])

    assert '"decision": "auto"' in asyncio.run(collect())


def test_client_param_can_still_reduce_when_lifted():
    """Even lifted, a client requesting REVIEW gets REVIEW (reduce-only holds)."""
    import asyncio

    from harness.hold import HoldRegistry
    from harness.state import AutonomyMode
    from main import _run_event_stream

    async def collect():
        return "".join([
            f async for f in _run_event_stream(
                "x", "lifted-2", "demo", AutonomyMode.REVIEW,
                hold_registry=HoldRegistry().lift("demo"),
            )
        ])

    assert '"decision": "review"' in asyncio.run(collect())
