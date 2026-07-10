"""Honest data-inventory readback (CustomerAcq-ju1.3, anti-theater core).

Before the supervisor plans a campaign it must state EXACTLY what data it has and does
NOT have — live from the database, never hardcoded, never fabricated. This is the one
shared builder both the chat host (``studio.agui``) and the voice supervisor
(``studio.voice``) call, so the two surfaces give the byte-identical readback (the
bead's no-divergence requirement).

Design:
  * counts are live ``COUNT(*)`` reads scoped by ``tenant_id`` (customers, artists,
    studios) + the ju1.2 campaign-example library — each returns ``int | None`` where
    ``None`` means "couldn't read this turn" (distinct from a genuine 0, so the readback
    never turns a store hiccup into a false "you have no data" claim);
  * field-presence keys off ACTUAL column population (does any customer carry a
    social handle / conversation history / interests?), NOT the tenant name — so a
    future CRM-rich tenant upgrades the readback honestly with zero code change;
  * the "what I don't have" sentence is derived from those presence flags: with no
    social + no conversation history on file, personalization is honestly limited to
    name/contact + campaign-level artist/offer strategy.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

_DEFAULT_DSN = "postgresql://scalers:scalers@localhost:5432/scalers"


def _dsn(dsn: str | None) -> str:
    return (dsn or os.environ.get("ENGINE_DATABASE_URL")
            or os.environ.get("DATABASE_URL") or _DEFAULT_DSN)


def _connect(dsn: str | None):
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(_dsn(dsn), row_factory=dict_row, autocommit=True)


def _scalar(dsn: str | None, sql: str, params: tuple) -> int | None:
    """Run a single COUNT query; return the int, or None if it could not be read
    (missing table / store down) — so the readback distinguishes "couldn't read" from
    a real 0 and never fabricates a count."""
    try:
        with _connect(dsn) as conn:
            row = conn.execute(sql, params).fetchone()
            return int(next(iter(row.values()))) if row else 0
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Field presence — keyed on actual column population, not tenant name.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class DataPresence:
    """Which customer signals actually exist for the tenant (each a real count).

    ``None`` means the probe could not run this turn (never asserted as absence)."""

    with_email: int | None = None
    with_phone: int | None = None
    with_social: int | None = None            # ig_handle OR linkedin_handle present
    with_conversation_history: int | None = None  # lead_conversations rows
    with_interests: int | None = None

    @property
    def has_social(self) -> bool:
        return bool(self.with_social)

    @property
    def has_conversation_history(self) -> bool:
        return bool(self.with_conversation_history)

    @property
    def has_interests(self) -> bool:
        return bool(self.with_interests)


@dataclass(frozen=True)
class DataInventory:
    """The real data on file for one tenant — every number a live DB read."""

    tenant_id: str
    customers: int | None = None
    artists: int | None = None
    studios: int | None = None
    examples: int | None = None
    example_artists: list[str] = field(default_factory=list)
    presence: DataPresence = field(default_factory=DataPresence)

    @property
    def readable(self) -> bool:
        """True iff at least the customer count came back — i.e. the DB answered this
        turn. When False the readback says so honestly instead of claiming zeros."""
        return self.customers is not None


def read_presence(tenant_id: str, *, dsn: str | None = None) -> DataPresence:
    """Probe which customer signals are actually populated for ``tenant_id``."""
    return DataPresence(
        with_email=_scalar(
            dsn, "SELECT count(*) FROM customers WHERE tenant_id=%s "
            "AND email IS NOT NULL AND email<>''", (tenant_id,)),
        with_phone=_scalar(
            dsn, "SELECT count(*) FROM customers WHERE tenant_id=%s "
            "AND phone IS NOT NULL AND phone<>''", (tenant_id,)),
        with_social=_scalar(
            dsn, "SELECT count(*) FROM customers WHERE tenant_id=%s AND ("
            "(ig_handle IS NOT NULL AND ig_handle<>'') OR "
            "(linkedin_handle IS NOT NULL AND linkedin_handle<>''))", (tenant_id,)),
        with_conversation_history=_scalar(
            dsn, "SELECT count(*) FROM lead_conversations WHERE tenant_id=%s", (tenant_id,)),
        with_interests=_scalar(
            dsn, "SELECT count(*) FROM customers WHERE tenant_id=%s "
            "AND interests IS NOT NULL AND cardinality(interests)>0", (tenant_id,)),
    )


def read_inventory(tenant_id: str, *, dsn: str | None = None) -> DataInventory:
    """Read the tenant's REAL data inventory — live counts + example artists + field
    presence. Each number is best-effort (None on read failure); nothing fabricated."""
    from studio.campaign_examples_store import get_examples

    try:
        examples = get_examples(tenant_id, dsn=dsn)
    except Exception:
        examples = []
    example_artists = sorted({
        (e.get("artist_name") or "").strip() for e in examples if (e.get("artist_name") or "").strip()
    })
    return DataInventory(
        tenant_id=tenant_id,
        customers=_scalar(dsn, "SELECT count(*) FROM customers WHERE tenant_id=%s", (tenant_id,)),
        artists=_scalar(dsn, "SELECT count(*) FROM artists WHERE tenant_id=%s", (tenant_id,)),
        studios=_scalar(
            dsn, "SELECT count(DISTINCT studio_name) FROM artist_studios WHERE artist_id IN "
            "(SELECT id FROM artists WHERE tenant_id=%s)", (tenant_id,)),
        examples=len(examples),
        example_artists=example_artists,
        presence=read_presence(tenant_id, dsn=dsn),
    )


# --------------------------------------------------------------------------- #
# Readback — PURE formatting over a DataInventory (unit-testable without a DB).
# --------------------------------------------------------------------------- #
def _fmt(n: int | None) -> str:
    return f"{n:,}" if isinstance(n, int) else "an unknown number of"


def build_inventory_readback(inv: DataInventory) -> str:
    """The honest data-inventory readback string for ``inv``. Pure — no I/O.

    States the real counts and, crucially, WHAT IS MISSING, so the supervisor never
    claims personalization it cannot ground. Identical bytes wherever it is rendered
    (chat + voice)."""
    if not inv.readable:
        return (
            "DATA INVENTORY: I could not read the studio's database this turn, so I will "
            "NOT quote any counts — I'd rather tell you I can't see it than guess. Ask me "
            "to try again, or check the connection."
        )

    p = inv.presence
    contact_bits: list[str] = []
    if isinstance(p.with_email, int):
        contact_bits.append(f"{p.with_email:,} with email")
    if isinstance(p.with_phone, int):
        contact_bits.append(f"{p.with_phone:,} with phone")
    contact = f" ({', '.join(contact_bits)})" if contact_bits else ""

    ex_artists = f" ({', '.join(inv.example_artists)})" if inv.example_artists else ""

    lines = [
        "DATA INVENTORY — the REAL data on file for this studio, live from the database "
        "(never estimated, never invented). State this honestly before planning:",
        f"- {_fmt(inv.customers)} customers{contact}",
        f"- {_fmt(inv.artists)} artists across {_fmt(inv.studios)} studios",
        f"- {_fmt(inv.examples)} previous campaign examples{ex_artists}",
    ]

    # What I do NOT have — derived from real field presence, never assumed.
    missing: list[str] = []
    if not p.has_conversation_history:
        missing.append("conversation history")
    if not p.has_social:
        missing.append("social profiles")
    if not p.has_interests:
        missing.append("per-customer interests")
    if missing:
        lines.append(
            "WHAT I DON'T HAVE: no " + ", ".join(missing) + " on file for these "
            "customers. So personalization is limited to name/contact + campaign-level "
            "artist/offer strategy — I will NOT claim per-customer tattoo interests, past "
            "bookings, objections, or social activity I cannot see. Upload CRM / "
            "conversation history to unlock deeper personalization."
        )
    else:
        lines.append(
            "PERSONALIZATION: this studio has richer per-customer signals on file "
            "(some of conversation history / social / interests) — you may ground "
            "personalization ONLY in a field that is actually present for that customer."
        )
    return "\n".join(lines)


def build_data_inventory(tenant_id: str, *, dsn: str | None = None) -> str:
    """Read the tenant's real inventory and render the honest readback. The single
    entry point both the chat host and the voice supervisor call so their readback can
    never diverge. Best-effort: an unreadable store yields the honest can't-read line,
    never a fabricated count.

    Appends the UNIVERSAL uploaded-FILES readback (nmh.4) — the real counts of every
    uploaded artifact (customer CSV, brand voice, documents, images, artwork) by type
    — so BOTH surfaces answer "can you see the CSV / brand voice / artwork — how many
    images?" from the same real state. Best-effort: the file registry being
    unreadable adds nothing (never a fabricated file)."""
    readback = build_inventory_readback(read_inventory(tenant_id, dsn=dsn))
    try:
        from studio.artifacts import artifact_inventory, build_artifacts_readback

        files = build_artifacts_readback(artifact_inventory(tenant_id, dsn=dsn))
    except Exception:
        files = ""
    return f"{readback}\n\n{files}" if files else readback


def live_operations_block(tenant_id: str, *, dsn: str | None = None) -> str:
    """The LIVE review-queue + run state as a per-turn context block — the single
    builder BOTH the chat host and the voice supervisor inject, so neither surface
    can state an operational number the database doesn't back (a browser audit
    caught the host claiming "0 drafts" against 7 pending rows). Best-effort: an
    unreadable store yields the honest unavailable-line, never a guessed count."""
    import os

    try:
        import psycopg

        conninfo = dsn or os.environ.get(
            "ENGINE_DATABASE_URL", "postgresql://scalers:scalers@localhost:5432/scalers"
        )
        with psycopg.connect(conninfo, autocommit=True, connect_timeout=3) as conn:
            pending = conn.execute(
                "SELECT coalesce(channel,'?') AS ch, count(*) FROM actions "
                "WHERE tenant_id = %s AND status = 'pending' GROUP BY 1 ORDER BY 2 DESC",
                (tenant_id,),
            ).fetchall()
            scheduled = conn.execute(
                "SELECT count(*) FROM actions WHERE tenant_id = %s "
                "AND status = 'pending' AND scheduled_for IS NOT NULL",
                (tenant_id,),
            ).fetchone()[0]
            last_run = conn.execute(
                "SELECT run_id, status, created_at FROM runs WHERE tenant_id = %s "
                "ORDER BY created_at DESC LIMIT 1",
                (tenant_id,),
            ).fetchone()
        total = sum(n for _, n in pending)
        by_ch = ", ".join(f"{ch}: {n}" for ch, n in pending) or "none"
        last = (
            f"latest run {last_run[0]} — {last_run[1]} ({last_run[2]:%Y-%m-%d %H:%M} UTC)"
            if last_run else "no runs recorded yet"
        )
        return (
            "LIVE OPERATIONS STATE (computed from the database THIS turn — these are "
            "the ONLY review-queue / run numbers you may state; for anything not "
            "listed here call the matching live-state tool instead of estimating):\n"
            f"- review queue: {total} pending draft(s) awaiting approval ({by_ch}); "
            f"{scheduled} scheduled\n"
            f"- {last}"
        )
    except Exception as exc:  # noqa: BLE001 — honest degradation beats a guess
        return (
            "LIVE OPERATIONS STATE unavailable this turn "
            f"({type(exc).__name__}) — say so if asked about the queue; never guess counts."
        )
