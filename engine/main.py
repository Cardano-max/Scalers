"""FastAPI surface for the engine (HARN-01 demo).

Exposes a liveness probe and a demo run endpoint that drives the fixed graph
end to end (Research -> Assemble) and routes the result through the pure-code
router. This is the thin edge over the deterministic core; it adds no control
logic of its own.
"""

from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel, Field

from harness.config import get_settings
from harness.graph import get_graph
from harness.router import route
from harness.state import AutonomyMode, GraphState, RouteDecision

app = FastAPI(title="Scalers Growth Engine", version="0.1.0")


class HealthResponse(BaseModel):
    status: str
    models: dict[str, str]
    temperature: float
    checkpointer: str


class RunRequest(BaseModel):
    topic: str = Field(..., min_length=1)
    thread_id: str = Field(..., min_length=1)
    tenant_id: str = "demo"
    autonomy: AutonomyMode = AutonomyMode.AUTO


class RunResponse(BaseModel):
    run_id: str
    topic: str
    draft: str
    confidence: float
    decision: RouteDecision
    step_log: list[str]


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


@app.post("/runs", response_model=RunResponse)
async def create_run(request: RunRequest) -> RunResponse:
    """Run the fixed graph for ``topic`` and route the result.

    Deterministic: the same ``topic`` always yields the same draft, confidence,
    and decision. The ``thread_id`` keys the checkpointer so a run is resumable.
    """

    graph = get_graph()
    init = GraphState(
        tenant_id=request.tenant_id,
        run_id=request.thread_id,
        topic=request.topic,
    )
    final = await graph.run(request.thread_id, init)

    confidence = final.confidence or 0.0
    decision = route(confidence, autonomy=request.autonomy)
    assert final.assembled is not None  # Assemble always runs in the fixed graph
    return RunResponse(
        run_id=request.thread_id,
        topic=final.assembled.topic,
        draft=final.assembled.draft,
        confidence=confidence,
        decision=decision,
        step_log=final.step_log,
    )
