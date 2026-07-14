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
    # P2 interactive Slice 1: the studio dev FE runs on a non-3000 port so it
    # never collides with the frozen demo on :3000. Allow it to reach /graphql.
    "http://localhost:3030",
    "http://127.0.0.1:3030",
    # P3.1 interactive studio: the chat-first Campaign Studio dev FE runs on :3002
    # and reaches the AG-UI endpoint (POST /studio/agui) + /graphql cross-origin.
    # Direct (not same-origin-proxied) so the SSE stream is never buffered by a dev
    # proxy. CORS is applied app-wide below, so it also covers /studio/agui.
    "http://localhost:3002",
    "http://127.0.0.1:3002",
    # Go-live boot: the integrated Campaign Studio web runs on :3031 (backend :8010),
    # isolated from the operator's :3000/:8000/:3030. Allowed here so a DIRECT
    # cross-origin bind (NEXT_PUBLIC_STUDIO_AGUI_URL=http://127.0.0.1:8010/...) also
    # works; the default boot uses the same-origin Next proxy (no CORS needed).
    "http://localhost:3031",
    "http://127.0.0.1:3031",
]

# DEPLOYED console origins (e.g. the Vercel URL when the console and engine live on
# different hosts): comma-separated in CONSOLE_ORIGINS, appended to the local dev
# list above. Explicit origins only — never a wildcard (allow_credentials=True).
import os as _os  # noqa: E402 — tiny, deliberate: keep the list definition together

CONSOLE_ORIGINS += [
    o.strip().rstrip("/")
    for o in _os.environ.get("CONSOLE_ORIGINS", "").split(",")
    if o.strip()
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
