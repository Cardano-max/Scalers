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

# Mount the operator-console obs-API (OBS-04): GraphQL at POST /graphql, SSE at
# GET /sse/stream, CORS for the Next.js console on :3000. Kept in its own package
# (engine/obsapi) so the thin portal above stays focused on the engine ingress.
from obsapi import mount_obsapi  # noqa: E402

mount_obsapi(app)

# Mount the Campaign Studio AG-UI agent (P3.1) at POST /studio/agui, ALONGSIDE the
# obs-API's /graphql + SSE. The existing Studio Host + role cells are wrapped in a
# pydantic-ai AGUIAdapter with an editable campaign-plan shared state and an
# approval gate. Import is deferred-safe (the route is added; the agent only calls
# a model at request time).
from studio.agui import mount_studio_agui  # noqa: E402

mount_studio_agui(app)

# Mount the speech-to-speech voice layer (P3 voice, OpenAI Realtime option B) at
# POST /studio/voice/{session,plan,orchestrate}. The voice agent is a pure FRONT-END
# (interviewer + narrator): the raw OPENAI_API_KEY stays server-side and only mints
# short-TTL ephemeral client secrets; the model gets a fixed send-incapable tool
# surface (plan edit + read-only state/leads) and a SERVER-SIDE 2-factor GO-gate guards the
# launch of the EXISTING held /studio/run spine. NOTHING is ever sent.
from studio.voice import mount_studio_voice  # noqa: E402

mount_studio_voice(app)

# Mount the ju1.5 console read API (campaign-example memory + draft lineage) —
# additive read-only endpoints the Review Queue and Campaign Memory views bind.
from studio.console_api import mount_console_api  # noqa: E402

mount_console_api(app)


@app.on_event("startup")
async def _announce_studio_tenant() -> None:
    """One loud line at boot: which tenant /studio/* serves. An engine started
    by hand without STUDIO_TENANT_ID silently served 'demo' and every studio
    surface read empty — this makes that state impossible to miss.

    It also NORMALIZES the value, which is not cosmetic. `set STUDIO_TENANT_ID=skindesign
    && uv run …` in cmd.exe captures the space before the `&&`, so the tenant becomes
    'skindesign ' — and every lookup keyed on it (the artwork library is
    `portfolio:{tenant}`) misses by exactly one character. The engine then ran perfectly,
    reported no error, and staged an Instagram post with NO IMAGE, because the portfolio
    read back empty. One stray space, a silently image-less post. Thirty-odd call sites
    read this variable; normalizing once at the boundary is the only place it can be made
    safe for all of them."""
    import os

    raw = os.environ.get("STUDIO_TENANT_ID")
    if raw is not None and raw != raw.strip():
        os.environ["STUDIO_TENANT_ID"] = raw.strip()
        print(
            f"[studio] STUDIO_TENANT_ID had surrounding whitespace ({raw!r}) — "
            f"normalized to {raw.strip()!r}. Unnormalized, every tenant-keyed read "
            "(artwork library, artists, review counts) would have missed and read EMPTY.",
            flush=True,
        )
    tenant = os.environ.get("STUDIO_TENANT_ID")
    if tenant:
        print(f"[studio] serving tenant: {tenant}", flush=True)
    else:
        print(
            "[studio] WARNING: STUDIO_TENANT_ID is not set — /studio/* routes serve "
            "the 'demo' tenant and any client data under other tenants will look "
            "EMPTY. Start via scripts/run-local.* or export STUDIO_TENANT_ID.",
            flush=True,
        )


@app.get("/studio/meta/verify")
def meta_verify_endpoint() -> dict:
    """LIVE Meta credential verification (the one-token activation probe): which
    META_* keys are set (booleans only, never values) and whether the token
    actually answers on the Graph API for the configured IG user + FB page.
    Failures return the real Graph error detail — never a fake 'verified'."""
    from studio.meta_status import meta_verify

    return meta_verify()


@app.get("/tenants/{tenant_id}")
def tenant_flags(tenant_id: str) -> dict:
    """Tenant safety flags (ju1.1): the server-side TEST-MODE state the console
    renders. 404-shaped honest null for an unregistered (legacy) tenant."""
    from tenants.store import get_tenant

    row = get_tenant(tenant_id)
    if row is None:
        return {"id": tenant_id, "registered": False, "testMode": None}
    return {"id": row["id"], "registered": True, "name": row["name"],
            "testMode": bool(row["test_mode"]),
            "allowlistSize": len(row.get("test_send_allowlist") or [])}


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
    studioTenant: str
    studioTenantExplicit: bool
    modelKeyPresent: bool


@app.get("/healthz", response_model=HealthResponse)
def healthz() -> HealthResponse:
    """Liveness + configuration probe.

    ``studioTenant`` is the tenant every ``/studio/*`` route serves. It is here
    because an engine started WITHOUT ``STUDIO_TENANT_ID`` silently fell back to
    'demo' and every studio surface (artists, artifacts, review counts) read
    empty while the real client data sat under another tenant — a whole class
    of 'the app is broken' reports. The console compares this value against its
    own tenant and warns loudly on mismatch; ``studioTenantExplicit`` is False
    when the fallback is in effect."""
    import os

    settings = get_settings()
    return HealthResponse(
        status="ok",
        models=settings.models.model_dump(),
        temperature=settings.temperature,
        checkpointer="postgres" if settings.database_url else "memory",
        studioTenant=os.environ.get("STUDIO_TENANT_ID", "demo"),
        studioTenantExplicit="STUDIO_TENANT_ID" in os.environ,
        # False = the LLM cells run on deterministic fallbacks and the VLM skips —
        # the single most common "nothing seems to work" cause on a fresh machine.
        modelKeyPresent=bool((os.environ.get("ANTHROPIC_API_KEY") or "").strip()),
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
        # Uncomputable confidence (None) fails safe to REVIEW explicitly (4jx.3) —
        # never coerced to 0.0: a zero threshold would auto-fire on it, and the SSE
        # frame would fabricate a "confidence": 0.0 for a value never computed
        # (None serializes to null = honest "uncomputable" for the console).
        confidence = values.get("confidence")
        if confidence is None:
            decision = RouteDecision.REVIEW
        else:
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
