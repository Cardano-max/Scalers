"""Supervisor MEMORY-STATE access (CustomerAcq-nmh.6, spec §17) — the voice supervisor
answers questions about real state from STORED data, never a guess.

"How many customers are loaded? Which artists? What old campaigns exist for this
artist? How many drafts are created and where are they in the Review Queue? What
failed? What did we do last time?" — every answer here is a pure read of durable
Postgres state (customers / campaign memory / actions), so it is correct after an
engine restart and never fabricated. When there is nothing to report, the honest
answer is zero / empty / ``None`` — never an invented campaign or count.
"""

from __future__ import annotations

from typing import Any

from studio.campaign_examples_store import _connect
from studio.campaign_memory import campaigns_for_artist, summarize_last

# The action status that means "waiting in the Review Queue for operator approval".
_REVIEW_QUEUE_STATUS = "pending"
_DRAFT_STATUSES = ("pending", "approved", "sent", "failed", "rejected")


def _customer_facts(tenant_id: str, dsn: str | None) -> tuple[int, list[str]]:
    """(customer count, distinct artist names on file) for the tenant — real rows only."""
    with _connect(dsn) as conn:
        total = conn.execute(
            "SELECT count(*) AS n FROM customers WHERE tenant_id = %s", (tenant_id,)
        ).fetchone()["n"]
        artist_rows = conn.execute(
            "SELECT DISTINCT artist FROM customers "
            "WHERE tenant_id = %s AND artist IS NOT NULL AND artist <> '' "
            "ORDER BY artist",
            (tenant_id,),
        ).fetchall()
    return int(total), [r["artist"] for r in artist_rows]


def _draft_counts(tenant_id: str, dsn: str | None) -> dict[str, int]:
    """Per-status draft counts from the real ``actions`` rows (the Review-Queue state)."""
    counts = {s: 0 for s in _DRAFT_STATUSES}
    try:
        with _connect(dsn) as conn:
            for r in conn.execute(
                "SELECT status, count(*) AS n FROM actions "
                "WHERE tenant_id = %s GROUP BY status",
                (tenant_id,),
            ).fetchall():
                if r["status"] in counts:
                    counts[r["status"]] = int(r["n"])
    except Exception:
        pass  # no actions table yet -> honest zeroes
    return counts


def memory_state(
    tenant_id: str, *, artist: str | None = None, dsn: str | None = None
) -> dict[str, Any]:
    """The supervisor's real-state answer bundle (spec §17). Pure reads; honest zeroes /
    empties when nothing is stored. When ``artist`` is given, also reports that artist's
    stored campaigns + a "last time we ran ..." summary the supervisor can speak."""
    customers_total, artists = _customer_facts(tenant_id, dsn)
    drafts = _draft_counts(tenant_id, dsn)
    state: dict[str, Any] = {
        "tenant_id": tenant_id,
        "customers_total": customers_total,
        "artists": artists,
        "drafts": drafts,
        "review_queue": drafts.get(_REVIEW_QUEUE_STATUS, 0),
        "failed": drafts.get("failed", 0),
    }
    if artist is not None:
        campaigns = campaigns_for_artist(tenant_id, artist, dsn=dsn)
        state["artist"] = artist
        state["campaigns_for_artist"] = len(campaigns)
        state["last_campaign_summary"] = summarize_last(tenant_id, artist, dsn=dsn)
    return state


def what_did_we_do_last_time(
    tenant_id: str, artist: str | None = None, *, dsn: str | None = None
) -> str | None:
    """The supervisor's answer to "what did we do last time (for this artist)?" —
    grounded in the last stored campaign, or ``None`` when there is no campaign memory
    (an honest "nothing on record", never a guessed history)."""
    return summarize_last(tenant_id, artist, dsn=dsn)
