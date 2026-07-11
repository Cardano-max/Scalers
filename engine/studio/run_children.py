"""Multi-channel child-run resolution — parent run id ↔ per-channel children.

A multi-channel launch (``studio.agui.start_registered_run``) fans a plan out into
ONE ISOLATED CHILD RUN PER CHANNEL under ``{parent_run_id}-{channel}``. The parent
id is the launch/debounce handle — it has NO agent_runs, NO actions and NO registry
entry of its own — so any surface polling the parent (the Agency live panel, the
voice supervisor's run status) used to read an empty run while the children did all
the work: the screen looked dead, the pause popups never surfaced, and the voice
host said "no drafts staged" over 6 staged drafts. A REAL operator hit exactly that.

This module is the ONE shared resolver those surfaces use:

  * :func:`parent_of` — ``team-camp_x-9f2…-ig`` → ``team-camp_x-9f2…`` (or None
    when the id has no channel suffix);
  * :func:`child_run_ids` — every child of a parent id, from the in-process runs
    registry (live) AND the durable rows (agent_runs / actions /
    artwork_selections / competitor_selections — so a child paused at a gate
    BEFORE its first step is still found after an engine restart);
  * :func:`composite_status` — the honest parent status over the children's
    (any pause outranks running; running outranks a terminal; errors are never
    hidden by a sibling's success).

HONESTY: discovery is exact-id matching (``{parent}-{channel token}`` against the
known channel vocabulary), never a fuzzy LIKE — a foreign run id can never be
claimed as someone's child.
"""

from __future__ import annotations

import os
from typing import Any

_DEFAULT_DSN = "postgresql://scalers:scalers@localhost:5432/scalers"

#: Every channel token a plan's ``channels`` list can carry (raw operator words +
#: the coercers' canonical forms). Child run ids are ``{parent}-{token}``, so this
#: vocabulary IS the child-id search space. Order = display order (messages first).
CHANNEL_SUFFIXES: tuple[str, ...] = (
    "email", "gmail", "sms",
    "ig", "instagram", "insta", "reels", "story", "stories",
    "fb", "facebook", "messenger",
    "tiktok",
)

_SUFFIX_SET = frozenset(CHANNEL_SUFFIXES)


def parent_of(run_id: str | None) -> str | None:
    """The parent run id when ``run_id`` is a per-channel child (its last dash
    segment is a known channel token), else ``None``. Pure."""
    if not run_id:
        return None
    head, sep, tail = run_id.rpartition("-")
    if sep and head and tail.lower() in _SUFFIX_SET:
        return head
    return None


def _dsn(dsn: str | None) -> str:
    return dsn or os.environ.get("ENGINE_DATABASE_URL") or _DEFAULT_DSN


def child_run_ids(
    run_id: str,
    *,
    registry: dict[str, Any] | None = None,
    dsn: str | None = None,
) -> list[str]:
    """Every child run of ``run_id``, in channel-vocabulary order. Sources, merged:

    * the in-process runs registry (live children, including one still mid-gate);
    * the durable rows — ``agent_runs`` + ``actions`` + the two selection tables
      (a child can PAUSE at the competitor/artwork gate before writing any step
      or action, so the selection rows are load-bearing after a restart).

    Exact-id matching only (``{run_id}-{token}``). ``[]`` for a single-channel
    run — the caller then reads ``run_id`` itself, unchanged."""
    if not run_id:
        return []
    candidates = [f"{run_id}-{token}" for token in CHANNEL_SUFFIXES]
    found: set[str] = set()
    for key in registry or {}:
        if key in candidates:
            found.add(key)
    try:
        import psycopg

        with psycopg.connect(_dsn(dsn), autocommit=True, connect_timeout=5) as conn:
            for table in (
                "agent_runs", "actions", "artwork_selections", "competitor_selections"
            ):
                try:
                    rows = conn.execute(
                        f"SELECT DISTINCT run_id FROM {table} WHERE run_id = ANY(%s)",  # noqa: S608 — fixed table names
                        (candidates,),
                    ).fetchall()
                except Exception:
                    continue  # a not-yet-created table is honest-empty, not an error
                for (rid,) in rows:
                    found.add(str(rid))
    except Exception:
        pass  # DB down → registry-only view (still live for in-process children)
    return [c for c in candidates if c in found]


#: Parent-status priority over the children's statuses. A pause needs the operator
#: NOW (it outranks everything); a running sibling keeps the whole launch live; an
#: error is never hidden behind a sibling's success once nothing is still moving.
_STATUS_PRIORITY: tuple[str, ...] = (
    "awaiting_selection", "running", "error", "failed", "completed", "not_built",
)


def composite_status(statuses: list[str | None]) -> str:
    """The honest aggregate status for a parent over its children. Pure."""
    have = {str(s) for s in statuses if s}
    for status in _STATUS_PRIORITY:
        if status in have:
            return status
    return "unknown"
