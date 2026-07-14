"""Anti-fake-personalization guard (CustomerAcq-ju1.3) — DB-free, hermetic.

Pins the deterministic net that catches a claim the copy makes about the customer that
no fact backs. A history-less customer (no interests / social / conversation) must trip
EVERY category; the same claim on a lead who genuinely has that field must pass.
"""

from __future__ import annotations

from cells.personalization_guard import (
    CLAIM_LABELS,
    facts_view,
    find_personalization_claims,
    personalization_ok,
    personalization_violations,
)

# The history-less skindesign customer: contact only, nothing to personalize on.
_HISTORYLESS = {"name": "Sam", "email": "sam@example.com", "phone": "+15551234567"}


# ── fabricated claims on a history-less customer -> violation ──────────────────


def test_fake_instagram_claim_blocked_for_historyless():
    v = personalization_violations(
        "Hey Sam! I saw your Instagram and loved your feed — book with us.", _HISTORYLESS)
    assert any("social" in x for x in v)


def test_fake_interest_claim_blocked():
    v = personalization_violations(
        "Based on your interest in fine-line florals, here's an idea.", _HISTORYLESS)
    assert any("interest" in x for x in v)


def test_fake_history_claim_blocked():
    v = personalization_violations(
        "It's been a while since your last tattoo — ready for the next piece?", _HISTORYLESS)
    assert any("history" in x for x in v)


def test_fake_objection_claim_blocked():
    v = personalization_violations(
        "I know price was your concern, so here's a payment plan.", _HISTORYLESS)
    assert any("objection" in x for x in v)


def test_fake_artist_preference_claim_blocked():
    v = personalization_violations(
        "Your favorite artist has new openings this month.", _HISTORYLESS)
    assert any("artist_preference" in x for x in v)


def test_historyless_multiple_claims_each_reported():
    v = personalization_violations(
        "I saw your Instagram. Since your last tattoo, and your interest in blackwork, "
        "your favorite artist is open. I know cost was your concern.", _HISTORYLESS)
    labels = {label for label in CLAIM_LABELS if any(label in x for x in v)}
    assert labels == {"social", "history", "interest", "artist_preference", "objection"}


# ── the SAME claim is clean when the fact is genuinely present ─────────────────


def test_instagram_claim_ok_when_handle_present():
    facts = {**_HISTORYLESS, "ig_handle": "@sam_ink"}
    assert personalization_ok("I saw your Instagram — love it.", facts)


def test_interest_claim_ok_when_interests_present():
    facts = {**_HISTORYLESS, "interests": ["fine-line", "floral"]}
    assert personalization_ok("Based on your interest in fine-line work, here's an idea.", facts)


def test_history_claim_ok_when_tattoo_history_present():
    facts = {**_HISTORYLESS, "tattoo_history": [{"style": "blackwork"}]}
    assert personalization_ok("Since your last tattoo, we've added new flash.", facts)


def test_artist_claim_ok_when_artist_present():
    facts = {**_HISTORYLESS, "artist": "Angel"}
    assert personalization_ok("Your favorite artist Angel has openings.", facts)


def test_objection_claim_ok_when_measured_objection_present():
    facts = facts_view(_HISTORYLESS, objection="price")
    assert personalization_ok("I know price was your concern — here's a plan.", facts)


def test_objection_sentinel_none_found_still_blocks():
    facts = {**_HISTORYLESS, "primary_objection": "none-found"}
    v = personalization_violations("You mentioned cost was an issue.", facts)
    assert any("objection" in x for x in v)


# ── honest / grounded copy never trips the guard ──────────────────────────────


def test_generic_honest_copy_is_clean():
    assert personalization_ok(
        "Hi Sam! Our artists have new openings this month — reply BOOK to grab a spot. "
        "Reply STOP to opt out.", _HISTORYLESS)


def test_campaign_level_offer_copy_is_clean():
    # Angel full-day-special style copy (campaign-level, not per-customer history).
    assert personalization_ok(
        "ANGEL FULL-DAY SPECIAL — limited 5 spots at $1,200. Reply ANGEL to check "
        "availability. Reply STOP to opt out.", _HISTORYLESS)


def test_empty_facts_is_fail_closed():
    # No facts at all -> any personalization claim violates.
    assert personalization_violations("I saw your Instagram.", None)
    assert personalization_violations("Your last session was great.", {})


def test_find_claims_dedupes_by_category():
    claims = find_personalization_claims(
        "Your Instagram, your feed, and your posts are great.")
    assert claims == ["social"]  # one category despite three social phrases


def test_facts_view_reads_objection_from_profile():
    from types import SimpleNamespace as NS

    profile = NS(primary_objection=NS(value="timing"))
    view = facts_view(_HISTORYLESS, profile=profile)
    assert view["primary_objection"] == "timing"
    assert personalization_ok("Your hesitation about timing is understandable.", view)


# ── wiring: the agui staging chokepoint skips a faking draft, never stages it ──


def _fake_draft_fn(facts, *, goal="", **kw):
    return {
        "channel": "gmail", "target": f"{facts['customer_id']}@lead.example",
        "subject": "We miss you",
        "draft": "Hey! I saw your Instagram and loved your recent posts — book again?",
        "grounding": [], "customer_id": facts["customer_id"],
        "copy_model": "grounded_template",
    }


def test_provided_leads_skips_fake_personalization_draft(monkeypatch):
    # A history-less lead whose (hallucinated) copy claims "I saw your Instagram"
    # and CANNOT be repaired (the revise seam is unavailable) must be SKIPPED with
    # a concrete reason at the staging site — the fake never reaches the queue.
    import actions.store as store_mod
    import studio.customer_research as cr
    from studio.agui import _execute_provided_leads_sync

    from tests.test_provided_leads_real_team import _plan, _wire

    _wire(monkeypatch)
    staged: list[str] = []
    monkeypatch.setattr(
        store_mod, "record_pending_action",
        lambda **kw: (staged.append(kw["idempotency_key"]) or f"act_{kw['idempotency_key']}"),
    )
    monkeypatch.setattr(cr, "build_outreach_draft", _fake_draft_fn)
    # Repair unavailable (hermetic: never a live rewrite in a unit test).
    monkeypatch.setattr(cr, "revise_outreach_draft", lambda t, d, c: None)
    summary = _execute_provided_leads_sync(_plan(), "sess1", "ladies8391", None, None)

    assert not staged, "draft that faked personalization reached the pending queue"
    reasons = " | ".join(s.get("reason", "") for s in summary["output_ledger"]["skipped"])
    assert "fake personalization" in reasons and "social" in reasons
    assert summary["n_pending"] == 0


def test_provided_leads_repairs_fake_personalization_when_rewrite_is_clean(monkeypatch):
    # NEW contract (operator order 2026-07-14, "fix the fakes"): a faking draft
    # gets ONE de-fabrication rewrite; a rewrite that passes the SAME guard stages
    # (the lead keeps its draft), and the staged copy is the CLEAN version — the
    # fake text itself never reaches the queue. A still-dirty rewrite skips (the
    # guard re-check is the arbiter, proven by the still-faking case below).
    import actions.store as store_mod
    import studio.customer_research as cr
    from studio.agui import _execute_provided_leads_sync

    from tests.test_provided_leads_real_team import _plan, _wire

    _wire(monkeypatch)
    staged_drafts: list[str] = []
    monkeypatch.setattr(
        store_mod, "record_pending_action",
        lambda **kw: (staged_drafts.append(kw["draft"]) or f"act_{kw['idempotency_key']}"),
    )
    monkeypatch.setattr(cr, "build_outreach_draft", _fake_draft_fn)
    monkeypatch.setattr(
        cr, "revise_outreach_draft",
        lambda t, d, c: {"subject": "Hello from the studio",
                         "draft": "Wanted to reach out and say hello. Reply STOP to opt out.",
                         "copy_model": "anthropic:claude-haiku-4-5"},
    )
    summary = _execute_provided_leads_sync(_plan(), "sess2", "ladies8391", None, None)

    assert staged_drafts, "repairable draft was dropped instead of repaired"
    assert all("saw your Instagram" not in d for d in staged_drafts), (
        "the FAKE copy reached the queue — the repair must stage the clean rewrite")
    reasons = " | ".join(s.get("reason", "") for s in summary["output_ledger"]["skipped"])
    assert "fake personalization" not in reasons


def test_provided_leads_still_skips_when_rewrite_still_fakes(monkeypatch):
    # The rewrite itself is re-checked by the SAME guard: a rewrite that still
    # fakes (here: still claims Instagram) is discarded and the lead skips.
    import actions.store as store_mod
    import studio.customer_research as cr
    from studio.agui import _execute_provided_leads_sync

    from tests.test_provided_leads_real_team import _plan, _wire

    _wire(monkeypatch)
    staged: list[str] = []
    monkeypatch.setattr(
        store_mod, "record_pending_action",
        lambda **kw: (staged.append(kw["idempotency_key"]) or f"act_{kw['idempotency_key']}"),
    )
    monkeypatch.setattr(cr, "build_outreach_draft", _fake_draft_fn)
    monkeypatch.setattr(
        cr, "revise_outreach_draft",
        lambda t, d, c: {"subject": "We miss you",
                         "draft": "Loved your Instagram feed! Reply STOP to opt out.",
                         "copy_model": "anthropic:claude-haiku-4-5"},
    )
    summary = _execute_provided_leads_sync(_plan(), "sess3", "ladies8391", None, None)

    assert not staged, "still-faking rewrite reached the pending queue"
    reasons = " | ".join(s.get("reason", "") for s in summary["output_ledger"]["skipped"])
    assert "fake personalization" in reasons


def test_provided_leads_stages_grounded_draft_normally(monkeypatch):
    # Control: a clean, non-faking draft is NOT skipped by the personalization guard.
    import actions.store as store_mod
    import studio.customer_research as cr
    from studio.agui import _execute_provided_leads_sync

    from tests.test_provided_leads_real_team import _plan, _wire

    _wire(monkeypatch)
    staged: list[str] = []
    monkeypatch.setattr(
        store_mod, "record_pending_action",
        lambda **kw: (staged.append(kw["idempotency_key"]) or f"act_{kw['idempotency_key']}"),
    )

    def _clean_draft(facts, *, goal="", **kw):
        return {
            "channel": "gmail", "target": f"{facts['customer_id']}@lead.example",
            "subject": "New openings this month",
            "draft": "Hi! Our artists have new openings — reply BOOK to grab a spot.",
            "grounding": [], "customer_id": facts["customer_id"],
            "copy_model": "grounded_template",
        }

    monkeypatch.setattr(cr, "build_outreach_draft", _clean_draft)
    _execute_provided_leads_sync(_plan(), "sess1", "ladies8391", None, None)
    assert len(staged) == 2  # both leads staged; the guard did not false-positive
