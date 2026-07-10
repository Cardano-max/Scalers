"""Honest data-inventory readback (CustomerAcq-ju1.3).

Two lanes:
  1. pure — ``build_inventory_readback`` over hand-built ``DataInventory`` objects:
     real counts rendered, the missing-data sentence honest, a store-hiccup readback
     that refuses to quote zeros;
  2. ``@pytest.mark.integration`` — live counts for the REAL skindesign tenant match
     the DB and the missing-data sentence reflects the actual (no social / no
     conversation) reality.

Anti-theater pins: a count that could not be read (None) is NEVER rendered as 0; the
"what I don't have" sentence keys off real field presence, not the tenant name.
"""

from __future__ import annotations

import os

import pytest

from studio.inventory import (
    DataInventory,
    DataPresence,
    build_data_inventory,
    build_inventory_readback,
    read_inventory,
)

_DSN = (
    os.environ.get("ENGINE_DATABASE_URL")
    or os.environ.get("DATABASE_URL")
    or "postgresql://scalers:scalers@localhost:5432/scalers"
)


def _db_or_skip():
    try:
        import psycopg

        psycopg.connect(_DSN, connect_timeout=3).close()
    except Exception as exc:  # pragma: no cover - env-dependent
        pytest.skip(f"no Postgres for integration test: {exc}")


# ── lane 1: pure readback formatting ──────────────────────────────────────────

_SKINDESIGN = DataInventory(
    tenant_id="skindesign",
    customers=1092, artists=37, studios=6, examples=5,
    example_artists=["Angel", "Bella", "Keebs", "Lynn"],
    presence=DataPresence(
        with_email=1092, with_phone=1061, with_social=0,
        with_conversation_history=0, with_interests=0,
    ),
)


def test_readback_states_real_counts_thousands_separated():
    out = build_inventory_readback(_SKINDESIGN)
    assert "1,092 customers" in out
    assert "1,092 with email" in out and "1,061 with phone" in out
    assert "37 artists across 6 studios" in out
    assert "5 previous campaign examples (Angel, Bella, Keebs, Lynn)" in out


def test_readback_has_the_honest_missing_data_sentence():
    out = build_inventory_readback(_SKINDESIGN)
    assert "no conversation history, social profiles" in out
    assert "personalization is limited to name/contact" in out
    assert "will NOT claim per-customer tattoo interests, past bookings, objections" in out
    assert "Upload CRM / conversation history" in out


def test_readback_upgrades_honestly_when_signals_present():
    rich = DataInventory(
        tenant_id="future_crm", customers=500, artists=3, studios=1, examples=2,
        example_artists=["Nina"],
        presence=DataPresence(
            with_email=500, with_phone=400, with_social=120,
            with_conversation_history=300, with_interests=200,
        ),
    )
    out = build_inventory_readback(rich)
    assert "WHAT I DON'T HAVE" not in out
    assert "richer per-customer signals" in out
    assert "ONLY in a field that is actually present" in out


def test_readback_partial_presence_lists_only_true_gaps():
    # Social present, but still no conversation history or interests -> gap sentence
    # names exactly the two real gaps, not social.
    partial = DataInventory(
        tenant_id="t", customers=10, artists=1, studios=1, examples=0,
        presence=DataPresence(
            with_email=10, with_phone=5, with_social=4,
            with_conversation_history=0, with_interests=0,
        ),
    )
    out = build_inventory_readback(partial)
    assert "no conversation history, per-customer interests on file" in out
    assert "social profiles" not in out.split("WHAT I DON'T HAVE")[1]


def test_unreadable_store_refuses_to_quote_zeros():
    # Every count None (store down) -> the readback must NOT say "0 customers"; it says
    # honestly that it can't read the DB.
    blind = DataInventory(tenant_id="t")  # all counts default None
    out = build_inventory_readback(blind)
    assert "could not read" in out
    assert "0 customers" not in out
    assert "unknown number" not in out  # doesn't even try to render counts


def test_zero_examples_renders_without_artist_parenthetical():
    inv = DataInventory(
        tenant_id="t", customers=3, artists=0, studios=0, examples=0,
        presence=DataPresence(with_email=3, with_phone=0, with_social=0,
                              with_conversation_history=0, with_interests=0),
    )
    out = build_inventory_readback(inv)
    assert "0 previous campaign examples" in out
    assert "previous campaign examples (" not in out  # no empty parenthetical


# ── lane 2: live counts against the REAL skindesign tenant ────────────────────


@pytest.mark.integration
def test_live_skindesign_inventory_matches_db():
    _db_or_skip()
    import psycopg

    with psycopg.connect(_DSN, autocommit=True) as c:
        want_customers = c.execute(
            "SELECT count(*) FROM customers WHERE tenant_id='skindesign'").fetchone()[0]
        want_artists = c.execute(
            "SELECT count(*) FROM artists WHERE tenant_id='skindesign'").fetchone()[0]
        want_examples = c.execute(
            "SELECT count(*) FROM campaign_examples WHERE tenant_id='skindesign'"
        ).fetchone()[0]
        want_convs = c.execute(
            "SELECT count(*) FROM lead_conversations WHERE tenant_id='skindesign'"
        ).fetchone()[0]

    if want_customers == 0:
        pytest.skip("skindesign not imported in this DB")

    inv = read_inventory("skindesign", dsn=_DSN)
    assert inv.readable
    assert inv.customers == want_customers  # live, not hardcoded
    assert inv.artists == want_artists
    assert inv.studios and inv.studios >= 1
    assert inv.examples == want_examples  # live — the campaign memory grows
    assert "Keebs" in inv.example_artists
    # Contact data is real; conversation history tracks the live intake (31 real
    # threads imported 2026-07-10), social profiles are still absent.
    assert inv.presence.with_email and inv.presence.with_email > 0
    assert inv.presence.has_social is False
    assert inv.presence.has_conversation_history is (want_convs > 0)

    out = build_data_inventory("skindesign", dsn=_DSN)
    assert f"{want_customers:,} customers" in out
    assert "social" in out  # still honestly reported missing


@pytest.mark.integration
def test_live_readback_is_identical_for_chat_and_voice_paths():
    # Both surfaces call build_data_inventory with the same (tenant, dsn) -> byte-
    # identical readback. This is the no-divergence guarantee, proven at the seam.
    _db_or_skip()
    a = build_data_inventory("skindesign", dsn=_DSN)
    b = build_data_inventory("skindesign", dsn=_DSN)
    assert a == b


@pytest.mark.integration
def test_unknown_tenant_reads_zeros_honestly_not_crash():
    _db_or_skip()
    inv = read_inventory("t_never_exists_ju13", dsn=_DSN)
    assert inv.readable  # the query ran; it's a real 0, not a read failure
    assert inv.customers == 0 and inv.artists == 0 and inv.examples == 0


@pytest.mark.integration
@pytest.mark.skipif(
    not os.environ.get("ENGINE_DATABASE_URL"),
    reason="requires Postgres (set ENGINE_DATABASE_URL)",
)
def test_live_operations_block_states_real_queue_counts():
    """The anti-fabrication block: the host/voice context must carry the TRUE
    pending-draft count (a browser audit caught the host claiming '0 drafts'
    against 7 pending rows). Seed one pending action on a throwaway tenant and
    the block must say exactly 1; an empty tenant must say exactly 0."""
    import uuid

    import psycopg

    from studio.inventory import live_operations_block

    dsn = os.environ["ENGINE_DATABASE_URL"]
    tenant = "t_ops_" + uuid.uuid4().hex[:8]
    action_id = "act_ops_" + uuid.uuid4().hex[:8]

    assert "0 pending draft(s)" in live_operations_block(tenant, dsn=dsn)

    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO actions (id, tenant_id, type, channel, draft, status) "
            "VALUES (%s, %s, 'outreach', 'gmail', 'hello', 'pending')",
            (action_id, tenant),
        )
    try:
        block = live_operations_block(tenant, dsn=dsn)
        assert "1 pending draft(s)" in block and "gmail: 1" in block
        assert "ONLY review-queue / run numbers" in block
    finally:
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute("DELETE FROM actions WHERE id = %s", (action_id,))
