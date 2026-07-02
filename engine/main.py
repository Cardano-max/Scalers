"""Thin FastAPI portal for the engine (HARN-01).

FastAPI is **not** the engine — the LangGraph StateGraph is. This surface is the
thin ingress/egress only:

* ``GET  /healthz``        — liveness + config probe.
* ``POST /webhooks/{src}`` — inbound webhook ingress (acknowledge + hand off).
* ``GET  /runs/stream``    — SSE out: relays the LangGraph run's per-node frames.

The SSE endpoint adds no control logic — it iterates the graph's own event
stream and forwards each frame. The graph owns flow; the portal only forwards.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi import FastAPI, Query
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

import metrics
from harness.config import get_settings
from harness.graph import get_graph
from harness.router import route
from harness.hold import DEFAULT_HOLD_REGISTRY, HoldRegistry
from harness.state import AutonomyMode, GraphState, RouteDecision

app = FastAPI(title="Scalers Growth Engine — portal", version="0.1.0")


@app.get("/metrics")
def metrics_endpoint() -> Response:
    """Prometheus scrape endpoint (13u) — the 3bu stack scrapes /metrics on :8000.

    Served at the exact path (a direct route, not a mounted sub-app) so there is
    no trailing-slash redirect for Prometheus to choke on.
    """
    data, content_type = metrics.render()
    return Response(content=data, media_type=content_type)


class HealthResponse(BaseModel):
    status: str
    models: dict[str, str]
    temperature: float
    checkpointer: str


@app.get("/healthz", response_model=HealthResponse)
def healthz() -> HealthResponse:
    """Liveness + configuration probe."""

    settings = get_settings()
    return HealthResponse(
        status="ok",
        models=settings.models.model_dump(),
        temperature=settings.temperature,
        checkpointer="postgres" if settings.database_url else "memory",
    )


class WebhookAck(BaseModel):
    run_id: str
    source: str
    status: str


class WebhookPayload(BaseModel):
    topic: str = Field(..., min_length=1)
    thread_id: str = Field(..., min_length=1)


@app.post("/webhooks/{source}", status_code=202, response_model=WebhookAck)
def ingest_webhook(source: str, payload: WebhookPayload) -> WebhookAck:
    """Thin webhook ingress: acknowledge and hand off to the engine.

    Phase 1 only acknowledges the trigger (202). The durable enqueue into the
    engine is eng3's side-effect boundary (HARN-03/04); the real Meta/Gmail
    handlers land in Phase 6. The portal never runs business logic itself.
    """

    return WebhookAck(run_id=payload.thread_id, source=source, status="accepted")


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def _run_event_stream(
    topic: str,
    thread_id: str,
    tenant_id: str,
    autonomy: AutonomyMode,
    hold_registry: HoldRegistry = DEFAULT_HOLD_REGISTRY,
) -> AsyncIterator[str]:
    """Relay a LangGraph run as SSE frames: one per node, then the routed decision.

    SAFETY (4jx.13): ``autonomy`` is resolved SERVER-SIDE before any routing —
    the client's requested dial can only REDUCE autonomy, never select AUTO for a
    held tenant. The registry is the b3f fail-safe primitive (held unless an
    operator explicitly lifted), so with the default registry this endpoint can
    never emit a decision:auto frame or record an 'auto' metric, at any
    confidence. A query param is a REQUEST, not authority.
    """

    autonomy = hold_registry.effective_autonomy(autonomy, tenant_id, "posting")
    graph = await get_graph()
    init = GraphState(tenant_id=tenant_id, run_id=thread_id, topic=topic)

    # Time the whole run for scalers_run_latency_seconds (p50/p95/p99 panel).
    with metrics.time_run(tenant=tenant_id):
        async for update in graph.astream(thread_id, init):
            for node, channels in update.items():
                yield _sse(
                    "node",
                    {"node": node, "step_log": channels.get("step_log", [])},
                )

        snapshot = await graph.get_state(thread_id)
        values = snapshot.values
        confidence = values.get("confidence") or 0.0
        decision = route(confidence, autonomy=autonomy)

    # Record the run + its autonomy outcome (auto vs review) for the dashboard.
    metrics.record_run(tenant=tenant_id, status="completed")
    outcome = "auto" if decision is RouteDecision.AUTO else "review"
    metrics.record_decision(outcome, tenant=tenant_id, channel="posting")
    assembled = values["assembled"]
    yield _sse(
        "decision",
        {
            "run_id": thread_id,
            "topic": assembled.topic,
            "draft": assembled.draft,
            "confidence": confidence,
            "decision": decision.value,
        },
    )
    yield "data: [DONE]\n\n"


@app.get("/runs/stream")
def stream_run(
    topic: str = Query(..., min_length=1),
    thread_id: str = Query(..., min_length=1),
    tenant_id: str = Query("demo"),
    autonomy: AutonomyMode = Query(AutonomyMode.AUTO),
) -> StreamingResponse:
    """SSE out: run the fixed graph (Research -> Assemble) and stream its frames.

    The engine is the LangGraph graph; this endpoint only forwards its event
    stream and appends the final routing decision.
    """

    return StreamingResponse(
        _run_event_stream(topic, thread_id, tenant_id, autonomy),
        media_type="text/event-stream",
    )
