"""Studio chat persistence (P2 interactive Slice 1) — the write/read path for the
Campaign Studio conversation.

Mirrors ``autonomy/store.py``: a thin :class:`ChatStore` protocol with an
in-memory implementation for tests and a :class:`PostgresChatStore` for the
durable path. The console's chat panel renders these rows; the studio-host agent
turns carry the real model pin they were produced with.

Schema (one row per chat turn, ordered by ``seq`` within a session):

* ``studio_chat_turns`` — ``(id, session_id, seq, role, text, model, created_at)``.
  ``role`` is one of ``operator`` | ``host``; ``host`` rows carry the real model
  pin (e.g. ``anthropic:claude-haiku-4-5``), ``operator`` rows carry ``NULL``.

The DDL follows the additive ``CREATE TABLE IF NOT EXISTS`` pattern of
``autonomy/store.py`` so re-running ``setup()`` on an existing cluster is a no-op.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

# Authors of a studio chat turn. P2 shipped (operator, host); P3.1 adds the role
# cells that emit LABELED in-thread brainstorm messages (each carrying its own
# model pin): funnel_architect, copywriter, critic (independent pass), jury (Opus).
# P3.x adds the WIRED traced-run roles surfaced from a `run_campaign` (the Phase-A
# spine writes agent_runs as researcher/strategist/draft/critic/jury; we mirror each
# as a LABELED in-thread trace so the operator can watch what each agent thought).
VALID_ROLES: tuple[str, ...] = (
    "operator",
    "host",
    "funnel_architect",
    "copywriter",
    "critic",
    "jury",
    "researcher",
    # The customer-PSYCHOLOGY analyst (P1): runs per-lead before the draft, deciding
    # WHERE each customer sits (category + objection) from their real conversation/facts.
    "analyst",
    "strategist",
    # P1.5: the plan-first PLANNER — runs ONCE before the per-lead loop, decomposing the
    # interview intent into an executable CampaignBlueprint (targets/quota/offer-logic/
    # stop-conditions). Recorded as the FIRST agent_run; may also record a replan turn on
    # a measured contradiction. Routed to the best tier; never an outreach send.
    "planner",
    "draft",
    # P3.x: the Host's REAL extended-thinking trace (Anthropic ThinkingPart.content),
    # captured from the run result and persisted so a frontend thinking-view can show
    # genuine reasoning. Carries the host model pin; never an outreach send.
    "thinking",
)


@dataclass(frozen=True)
class ChatTurnRecord:
    """One persisted chat turn. ``model`` is the real pin for ``host`` rows and
    ``None`` for ``operator`` rows. ``created_at`` is an ISO-8601 string."""

    id: str
    session_id: str
    seq: int
    role: str
    text: str
    model: str | None
    created_at: str


def new_turn_id() -> str:
    return "turn_" + uuid.uuid4().hex


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@runtime_checkable
class ChatStore(Protocol):
    """Thin persistence interface for studio chat turns."""

    def setup(self) -> None: ...

    def append_turn(
        self, session_id: str, role: str, text: str, model: str | None = None
    ) -> ChatTurnRecord: ...

    def history(self, session_id: str) -> list[ChatTurnRecord]: ...


class InMemoryChatStore:
    """In-memory ``ChatStore`` for the demo and unit tests (no driver needed)."""

    def __init__(self) -> None:
        self._by_session: dict[str, list[ChatTurnRecord]] = {}

    def setup(self) -> None:  # no-op; symmetry with the Postgres store
        return None

    def append_turn(
        self, session_id: str, role: str, text: str, model: str | None = None
    ) -> ChatTurnRecord:
        if role not in VALID_ROLES:
            raise ValueError(f"role must be one of {VALID_ROLES}, got {role!r}")
        turns = self._by_session.setdefault(session_id, [])
        seq = (max((t.seq for t in turns), default=0)) + 1
        rec = ChatTurnRecord(
            id=new_turn_id(),
            session_id=session_id,
            seq=seq,
            role=role,
            text=text,
            model=model,
            created_at=_now_iso(),
        )
        turns.append(rec)
        return rec

    def history(self, session_id: str) -> list[ChatTurnRecord]:
        return sorted(self._by_session.get(session_id, []), key=lambda t: t.seq)


class PostgresChatStore:
    """Postgres ``ChatStore``. psycopg is imported lazily so the in-memory path
    needs no driver installed (mirrors ``autonomy/store.py``)."""

    def __init__(self, conninfo: str) -> None:
        import psycopg
        from psycopg.rows import dict_row

        self._connect = lambda: psycopg.connect(
            conninfo, autocommit=True, row_factory=dict_row
        )

    def setup(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS studio_chat_turns (
                    id          TEXT PRIMARY KEY,
                    session_id  TEXT        NOT NULL,
                    seq         INTEGER     NOT NULL,
                    role        TEXT        NOT NULL,
                    text        TEXT        NOT NULL,
                    model       TEXT,
                    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
                );
                CREATE INDEX IF NOT EXISTS studio_chat_turns_session_idx
                    ON studio_chat_turns (session_id, seq);
                """
            )
            # P3.1 migration: the P2 table shipped a narrow CHECK (operator|host).
            # Drop it (idempotent) so the role cells can log LABELED brainstorm
            # turns. Validity is enforced in Python by VALID_ROLES instead.
            conn.execute(
                "ALTER TABLE studio_chat_turns "
                "DROP CONSTRAINT IF EXISTS studio_chat_turns_role_check"
            )

    def append_turn(
        self, session_id: str, role: str, text: str, model: str | None = None
    ) -> ChatTurnRecord:
        if role not in VALID_ROLES:
            raise ValueError(f"role must be one of {VALID_ROLES}, got {role!r}")
        tid = new_turn_id()
        # Compute the next seq in the same statement as the insert so a single
        # session writer never reuses a seq (no read-then-write race window).
        with self._connect() as conn:
            row = conn.execute(
                """
                INSERT INTO studio_chat_turns (id, session_id, seq, role, text, model)
                SELECT %s, %s, COALESCE(MAX(seq), 0) + 1, %s, %s, %s
                FROM studio_chat_turns WHERE session_id = %s
                RETURNING id, session_id, seq, role, text, model, created_at
                """,
                (tid, session_id, role, text, model, session_id),
            ).fetchone()
        return _record_from(row)

    def history(self, session_id: str) -> list[ChatTurnRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, session_id, seq, role, text, model, created_at "
                "FROM studio_chat_turns WHERE session_id = %s ORDER BY seq",
                (session_id,),
            ).fetchall()
        return [_record_from(r) for r in rows]


def _record_from(row: dict[str, Any]) -> ChatTurnRecord:
    created = row["created_at"]
    return ChatTurnRecord(
        id=row["id"],
        session_id=row["session_id"],
        seq=row["seq"],
        role=row["role"],
        text=row["text"],
        model=row["model"],
        created_at=created.isoformat() if hasattr(created, "isoformat") else str(created),
    )
