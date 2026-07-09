"""SSE endpoint generator — ``GET /sse/stream?tenantId=...``.

Multiplexes the 7 canonical events from ``web/lib/data/sse.ts``
(``feed.event``, ``action.created``, ``action.updated``, ``run.updated``,
``kpi.updated``, ``health.updated``, ``toast``) as ``event:``/``data:`` frames.

For the demo this is a simple, non-blocking periodic poll: it emits an initial
``kpi.updated`` + ``health.updated`` snapshot, then on each tick emits any NEW
feed events and any recently-updated actions (``action.updated``). DB work runs
in a thread (``asyncio.to_thread``) so the event loop is never blocked.

Payloads are serialized to camelCase JSON (the SSE client `JSON.parse`s straight
into the typed models), via :func:`_to_json` over the strawberry dataclasses.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from collections.abc import AsyncIterator
from typing import Any

from . import repo

POLL_SECONDS = 3.0


def _camel(name: str) -> str:
    head, *tail = name.split("_")
    return head + "".join(part.title() for part in tail)


def _to_json(obj: Any) -> Any:
    """Recursively convert a strawberry dataclass instance to a camelCase dict."""

    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {
            _camel(f.name): _to_json(getattr(obj, f.name))
            for f in dataclasses.fields(obj)
        }
    if isinstance(obj, list):
        return [_to_json(x) for x in obj]
    return obj


def _frame(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(_to_json(data))}\n\n"


async def sse_stream(tenant_id: str, feed_only: bool = False) -> AsyncIterator[str]:
    """Yield SSE frames for ``tenant_id`` until the client disconnects."""

    seen_feed: set[str] = set()
    seen_actions: dict[str, str] = {}  # action id -> last seen status

    if not feed_only:
        try:
            yield _frame("kpi.updated", await asyncio.to_thread(repo.kpis, tenant_id))
            yield _frame(
                "health.updated",
                await asyncio.to_thread(repo.system_health, tenant_id),
            )
        except Exception:  # noqa: BLE001 — never tear the stream down on a bad poll
            pass

    while True:
        try:
            events = await asyncio.to_thread(repo.feed, tenant_id, 20)
            # Emit oldest-first so the client appends in chronological order.
            for ev in reversed(events):
                if ev.id not in seen_feed:
                    seen_feed.add(ev.id)
                    yield _frame("feed.event", ev)

            if not feed_only:
                actions = await asyncio.to_thread(repo.review_queue, tenant_id)
                for act in actions:
                    prev = seen_actions.get(act.id)
                    if prev is None:
                        seen_actions[act.id] = act.status
                        yield _frame("action.created", act)
                    elif prev != act.status:
                        seen_actions[act.id] = act.status
                        yield _frame("action.updated", act)
        except Exception:  # noqa: BLE001 — keep the connection alive across blips
            pass

        # SSE comment as a keepalive so proxies don't time the connection out.
        yield ": keepalive\n\n"
        await asyncio.sleep(POLL_SECONDS)
