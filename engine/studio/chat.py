"""Studio chat orchestration (P2 interactive Slice 1).

The thin service the GraphQL layer calls. One send does exactly:

  1. persist the operator turn (role='operator'),
  2. load the conversation history (including that turn),
  3. call the REAL studio-host agent with the history,
  4. persist the host turn (role='host', carrying the real model pin),
  5. return both turns.

The DB work is synchronous psycopg, so it is offloaded with ``asyncio.to_thread``
the same way the obs-API resolvers do; the LLM call is awaited directly.
"""

from __future__ import annotations

import asyncio
from functools import lru_cache

from studio.chat_store import ChatStore, ChatTurnRecord, PostgresChatStore
from studio.host_agent import HOST_MODEL, run_host


@lru_cache(maxsize=1)
def default_store() -> ChatStore:
    """The durable Postgres store, built once and table-ensured. Uses the same DSN
    the obs-API read model uses (``ENGINE_DATABASE_URL`` → ``DATABASE_URL`` → local
    demo cluster)."""
    from obsapi.db import get_dsn

    store = PostgresChatStore(get_dsn())
    store.setup()
    return store


async def send_chat_message(
    session_id: str, text: str, *, store: ChatStore | None = None
) -> tuple[ChatTurnRecord, ChatTurnRecord]:
    """Persist the operator turn, get a REAL host reply, persist it, return both."""
    store = store or default_store()

    operator = await asyncio.to_thread(store.append_turn, session_id, "operator", text, None)
    history = await asyncio.to_thread(store.history, session_id)

    reply_text = await run_host(history)

    host = await asyncio.to_thread(
        store.append_turn, session_id, "host", reply_text, HOST_MODEL
    )
    return operator, host


async def chat_history(
    session_id: str, *, store: ChatStore | None = None
) -> list[ChatTurnRecord]:
    """Return the ordered conversation for a session."""
    store = store or default_store()
    return await asyncio.to_thread(store.history, session_id)
