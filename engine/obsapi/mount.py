"""Mount the obs-API onto the engine's FastAPI app.

Adds CORS for the Next.js console (``http://localhost:3000``), the GraphQL router
at ``POST /graphql`` (GraphiQL on GET), and the SSE endpoints at
``GET /sse/stream`` and ``GET /sse/feed``.
"""

from __future__ import annotations

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from strawberry.fastapi import GraphQLRouter

from .schema import schema
from .sse import sse_stream

CONSOLE_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]


def mount_obsapi(app: FastAPI) -> None:
    """Wire CORS + ``/graphql`` + ``/sse/*`` onto ``app`` (idempotent per process)."""

    if getattr(app.state, "_obsapi_mounted", False):
        return
    app.state._obsapi_mounted = True

    app.add_middleware(
        CORSMiddleware,
        allow_origins=CONSOLE_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(GraphQLRouter(schema), prefix="/graphql")

    @app.get("/sse/stream")
    async def sse_stream_route(  # noqa: ANN202 — FastAPI route
        tenantId: str = Query(..., min_length=1),
    ) -> StreamingResponse:
        return StreamingResponse(
            sse_stream(tenantId),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/sse/feed")
    async def sse_feed_route(  # noqa: ANN202 — FastAPI route
        tenantId: str = Query(..., min_length=1),
    ) -> StreamingResponse:
        return StreamingResponse(
            sse_stream(tenantId, feed_only=True),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
