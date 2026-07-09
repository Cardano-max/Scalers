"""Campaign MEMORY library (CustomerAcq-nmh.6, spec §18) — our own completed runs are
stored as durable campaign memory and reused when generating the next campaign
("last time we ran X for this artist"), alongside operator-imported examples.

This deliberately REUSES the existing ``campaign_examples`` table
(:mod:`studio.campaign_examples_store`): its columns already ARE the §18 campaign-
memory schema (campaign_name / artist / offer price+type / recipient / delivered /
failed / dnd-blocked / copy / CTA / attachment / categories / location / sent_at /
status). Today only operator-imported screenshots populate it; this module lets the
ENGINE record its OWN runs there under ``source='engine_run'`` so both form ONE memory
library the supervisor and the generator read through one API.

HONESTY: a run-campaign row is a factual record of what a run STAGED (recipient/
delivered/failed counts come from the real run summary); nothing is fabricated, and an
artist with no stored campaign reads honestly as empty (``None`` / ``[]``). Persists to
the shared Postgres — it survives an engine restart (the table is durable).
"""

from __future__ import annotations

import json
from typing import Any

from studio.campaign_examples_store import (
    _connect,
    ensure_schema,
    get_examples,
)

# Marks a memory row that came from one of OUR engine runs (vs an imported operator
# screenshot). Distinct source so the library can show/attribute both honestly.
SOURCE_ENGINE_RUN = "engine_run"

# The subset of ``campaign_examples`` columns a recorded run populates. (Screenshot-only
# fields like ``source_screenshot`` / ``from_number`` stay NULL for an engine run.)
_RUN_COLS: tuple[str, ...] = (
    "campaign_name", "status", "sent_at", "artist_name", "offer_price_usd",
    "offer_type", "recipient_count", "delivered_count", "failed_count",
    "dnd_blocked_count", "message_copy", "cta", "attachment_present", "categories",
    "location",
)
_JSONB = frozenset({"categories"})


def _campaign_mem_id(tenant_id: str, run_id: str, campaign_name: str) -> str:
    """Deterministic id so re-recording the SAME run is idempotent (no duplicate rows)."""
    import hashlib

    h = hashlib.sha256(f"{tenant_id}|{run_id}|{campaign_name}".encode()).hexdigest()[:16]
    return f"cm_{h}"


def record_run_campaign(
    tenant_id: str,
    *,
    campaign_name: str,
    artist: str | None = None,
    offer_type: str | None = None,
    offer_price_usd: float | int | None = None,
    message_copy: str | None = None,
    cta: str | None = None,
    recipient_count: int | None = None,
    delivered_count: int | None = None,
    failed_count: int | None = None,
    blocked_count: int | None = None,
    attachment_present: bool | None = None,
    categories: list[str] | None = None,
    location: str | None = None,
    status: str | None = None,
    sent_at: str | None = None,
    run_id: str = "",
    dsn: str | None = None,
) -> str:
    """Record ONE completed run's campaign summary into the campaign-memory library.

    Idempotent on ``(tenant, run_id, campaign_name)`` — re-recording the same run
    refreshes its row rather than duplicating. Returns the row id. Best-effort caller
    contract: raises only on a real DB error (callers wrap it so a memory-write hiccup
    never breaks the actual run)."""
    if not (campaign_name or "").strip():
        raise ValueError("campaign_name is required")
    ensure_schema(dsn)
    row_id = _campaign_mem_id(tenant_id, run_id, campaign_name)
    values: dict[str, Any] = {
        "campaign_name": campaign_name, "status": status, "sent_at": sent_at,
        "artist_name": artist, "offer_price_usd": offer_price_usd,
        "offer_type": offer_type, "recipient_count": recipient_count,
        "delivered_count": delivered_count, "failed_count": failed_count,
        "dnd_blocked_count": blocked_count, "message_copy": message_copy, "cta": cta,
        "attachment_present": attachment_present, "categories": categories,
        "location": location,
    }
    cols = ", ".join(_RUN_COLS)
    placeholders = ", ".join("%s::jsonb" if c in _JSONB else "%s" for c in _RUN_COLS)
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in _RUN_COLS)
    params = [
        json.dumps(values[c]) if c in _JSONB and values[c] is not None else values[c]
        for c in _RUN_COLS
    ]
    provenance = json.dumps({"run_id": run_id}) if run_id else None
    with _connect(dsn) as conn:
        conn.execute(
            f"INSERT INTO campaign_examples (id, tenant_id, source, provenance, {cols}) "
            f"VALUES (%s, %s, %s, %s::jsonb, {placeholders}) "
            f"ON CONFLICT (id) DO UPDATE SET {updates}, "
            "source = EXCLUDED.source, provenance = EXCLUDED.provenance, "
            "updated_at = now()",
            [row_id, tenant_id, SOURCE_ENGINE_RUN, provenance, *params],
        )
    return row_id


def campaigns_for_artist(
    tenant_id: str, artist: str | None = None, *, dsn: str | None = None
) -> list[dict[str, Any]]:
    """All stored campaigns for an artist (engine runs + imported examples), newest
    first. Honest-empty (``[]``) for an artist/tenant with no campaign memory."""
    rows = get_examples(tenant_id, artist, dsn=dsn)
    # get_examples orders sent_at NULLS LAST asc; the supervisor wants NEWEST first.
    return sorted(
        rows, key=lambda r: (str(r.get("sent_at") or ""), str(r.get("created_at") or "")),
        reverse=True,
    )


def last_campaign(
    tenant_id: str, artist: str | None = None, *, dsn: str | None = None
) -> dict[str, Any] | None:
    """The most recent stored campaign for an artist, or ``None`` when none exists."""
    rows = campaigns_for_artist(tenant_id, artist, dsn=dsn)
    return rows[0] if rows else None


def summarize_last(
    tenant_id: str, artist: str | None = None, *, dsn: str | None = None
) -> str | None:
    """A grounded one-line reuse summary of the last campaign for an artist ("Last time
    we ran '<name>' ..."), or ``None`` when there is no stored campaign — the supervisor
    answers "what did we do last time" from THIS, never a guess."""
    last = last_campaign(tenant_id, artist, dsn=dsn)
    if last is None:
        return None
    who = last.get("artist_name") or artist or "this artist"
    bits: list[str] = [f"Last time we ran \"{last.get('campaign_name')}\" for {who}"]
    offer = last.get("offer_type")
    price = last.get("offer_price_usd")
    if offer or price:
        price_bit = f" ${int(price)}" if price not in (None, "") else ""
        bits.append(f"offer: {offer or 'special'}{price_bit}")
    if last.get("cta"):
        bits.append(f'CTA "{last["cta"]}"')
    rc = last.get("recipient_count")
    if rc not in (None, ""):
        dc = last.get("delivered_count")
        fc = last.get("failed_count")
        tail = f"{rc} recipients"
        if dc not in (None, "") or fc not in (None, ""):
            tail += f" ({dc or 0} delivered, {fc or 0} failed)"
        bits.append(tail)
    return "; ".join(bits) + "."
