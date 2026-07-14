"""Style-preference learning tests (client direction, PA meeting 2026-07-11).

Pure distillation (no DB) PLUS the closed trainable loop: capture via the real
edit mutation, persistence on the memories 'style' subject (idempotent per exact
edit), deterministic read-back, and the sent-action audit guard.
"""

from __future__ import annotations

import os
import uuid

import pytest

from studio.style_memory import (
    accumulate_preferences,
    learn_style_preference,
    load_style_preferences,
    record_style_edit,
    render_style_preferences_block,
)

_pg = pytest.mark.skipif(
    not os.environ.get("ENGINE_DATABASE_URL"),
    reason="requires Postgres (set ENGINE_DATABASE_URL)",
)


def test_single_edit_extracts_deterministic_signals():
    original = (
        "🔥🔥 HUGE 20% off flash sale!! Book now!! Limited spots!! "
        "DM us today for this once-in-a-lifetime deal. #tattoo #flash #sale"
    )
    edited = "Fresh fine-line flash available this month. Message us to book a slot."
    pref = learn_style_preference(original, edited)
    sig = set(pref["signals"])
    assert "shorter" in sig
    assert "no_emoji" in sig
    assert "less_hype" in sig
    assert "drop_discounts" in sig       # the 20% off was removed
    assert "fewer_hashtags" in sig


def test_non_change_infers_nothing():
    assert learn_style_preference("same text here", "same text here") == {
        "signals": [], "removed_phrases": []
    }
    assert learn_style_preference("draft", "") == {"signals": [], "removed_phrases": []}


def test_rule_promotion_requires_repetition():
    # One edit drops emoji -> a suggestion, not yet a rule.
    once = accumulate_preferences([("Nice piece 🔥", "Nice piece.")])
    assert "no_emoji" in once["suggestions"]
    assert once["rules"] == []
    # The operator does it twice -> it becomes a firm RULE.
    twice = accumulate_preferences([
        ("Nice piece 🔥", "Nice piece."),
        ("Healed 💉 and happy 😊", "Healed and happy."),
    ])
    assert "no_emoji" in twice["rules"]
    assert twice["edit_count"] == 2


def test_avoid_phrases_captured_verbatim_after_repetition():
    # The SAME line cut across two drafts -> a firm avoid-phrase (a one-off cut
    # stays a suggestion; repetition makes it a rule).
    edits = [
        ("Book now for the deal of a lifetime. Great work here.", "Great work here."),
        ("Book now for the deal of a lifetime. Fresh ink today.", "Fresh ink today."),
    ]
    prefs = accumulate_preferences(edits)
    assert any("deal of a lifetime" in p for p in prefs["avoid_phrases"])
    # A line cut only once does not reach the avoid threshold.
    once = accumulate_preferences(
        [("One-time filler line here. Keep this.", "Keep this.")]
    )
    assert once["avoid_phrases"] == []


def test_render_block_is_empty_without_signal_and_orders_learned_voice():
    assert render_style_preferences_block(None) == ""
    assert render_style_preferences_block(
        {"rules": [], "suggestions": [], "avoid_phrases": [], "edit_count": 0}
    ) == ""
    prefs = accumulate_preferences([
        ("Huge sale!! 20% off!!", "New flash available."),
        ("Massive promo!! discount inside!!", "Fresh work this week."),
    ])
    block = render_style_preferences_block(prefs)
    assert "OPERATOR STYLE PREFERENCES" in block
    assert "RULE:" in block
    assert "generic" in block


# -- the closed loop: persist -> reload -> rules; idempotent; audit guard -------- #


@pytest.mark.integration
@_pg
def test_recorded_edits_accumulate_into_rules_and_render():
    tenant = "t_style_" + uuid.uuid4().hex[:8]
    p1 = record_style_edit(tenant, "Huge sale!! 20% off!! 🔥", "New flash available this month.")
    assert "drop_discounts" in p1["signals"] and "no_emoji" in p1["signals"]
    record_style_edit(tenant, "Massive promo!! discount inside!! 😍", "Fresh work this week.")
    prefs = load_style_preferences(tenant)
    assert prefs is not None and prefs["edit_count"] == 2
    assert "drop_discounts" in prefs["rules"] and "no_emoji" in prefs["rules"]
    block = render_style_preferences_block(prefs)
    assert "OPERATOR STYLE PREFERENCES" in block and "RULE:" in block


@pytest.mark.integration
@_pg
def test_identical_edit_is_idempotent_and_non_change_stores_nothing():
    tenant = "t_style_" + uuid.uuid4().hex[:8]
    # The same exact edit retried (a re-sent mutation) must count ONCE.
    record_style_edit(tenant, "Nice piece 🔥", "Nice piece.")
    record_style_edit(tenant, "Nice piece 🔥", "Nice piece.")
    prefs = load_style_preferences(tenant)
    assert prefs is not None and prefs["edit_count"] == 1
    assert prefs["rules"] == []  # one edit is a suggestion, never a rule
    # A non-change stores nothing at all.
    assert record_style_edit(tenant, "same", "same") == {}


@pytest.mark.integration
@_pg
def test_edit_mutation_learns_and_sent_actions_refuse_edits():
    import psycopg

    from obsapi import repo

    dsn = os.environ["ENGINE_DATABASE_URL"]
    tenant = "t_style_" + uuid.uuid4().hex[:8]
    pending_id = "act_edit_" + uuid.uuid4().hex[:8]
    sent_id = "act_sent_" + uuid.uuid4().hex[:8]
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO actions (id, tenant_id, type, channel, draft, status) VALUES "
            "(%s, %s, 'outreach', 'gmail', %s, 'pending'), "
            "(%s, %s, 'outreach', 'gmail', 'delivered copy', 'sent')",
            (pending_id, tenant, "Huge flash sale!! 20% off!! 🔥🔥 Book now!!",
             sent_id, tenant),
        )
    try:
        # The REAL edit path: draft updated + the edit learned.
        out = repo.edit_action_draft(pending_id, "Fresh fine-line flash this month.")
        assert out is not None
        with psycopg.connect(dsn, autocommit=True) as conn:
            row = conn.execute(
                "SELECT draft FROM actions WHERE id=%s", (pending_id,)
            ).fetchone()
        assert row[0] == "Fresh fine-line flash this month."
        prefs = load_style_preferences(tenant)
        assert prefs is not None and prefs["edit_count"] == 1
        # AUDIT GUARD: a sent action's draft is the delivery record — editing refuses.
        with pytest.raises(ValueError):
            repo.edit_action_draft(sent_id, "rewritten history")
        with psycopg.connect(dsn, autocommit=True) as conn:
            row = conn.execute("SELECT draft FROM actions WHERE id=%s", (sent_id,)).fetchone()
        assert row[0] == "delivered copy"  # untouched
    finally:
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute("DELETE FROM actions WHERE id IN (%s, %s)", (pending_id, sent_id))


@pytest.mark.integration
@_pg
def test_brand_voice_context_carries_learned_preferences(monkeypatch):
    # The outreach choke point: once edits exist, resolve_brand_voice appends the
    # learned block so EVERY draft honors it. (Uses a stub pack so the voice side
    # resolves hermetically; the style side reads the REAL stored edits.)
    tenant = "t_style_" + uuid.uuid4().hex[:8]
    record_style_edit(tenant, "Big sale!! 🔥🔥 #a #b #c", "New work this month.")
    record_style_edit(tenant, "Don't miss out!! 😍 #x #y #z", "Fresh pieces available.")
    import studio.customer_research as cr

    monkeypatch.setattr(cr, "_render_voice_context", lambda dims: "VOICE BASE")
    import config.loader as loader

    class _Vocab:  # minimal stand-ins for the pack/dims surface
        approved_claims = ("real claim",)

    class _Dims:
        vocabulary = _Vocab()

    monkeypatch.setattr(loader, "load_pack", lambda tid, **k: object())
    import kb.voice as kv

    monkeypatch.setattr(kv, "load_voice_dimensions", lambda pack: _Dims())
    ctx, claims = cr.resolve_brand_voice(tenant)
    assert ctx.startswith("VOICE BASE")
    assert "OPERATOR STYLE PREFERENCES" in ctx and "no emoji" in ctx
    assert claims == ("real claim",)
