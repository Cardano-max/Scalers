"""Cohort-claim conformance + the sign-off identity gate (truth-gap fix 5).

The harness-confirmed defect: a 'Keebs price/timing winback' silently selected 3
non-Keebs leads with non-price objections, drafted under the Keebs angle anyway, and
signed 'Cheers, Keebs'. Two honest seams pin the fix:

* :func:`studio.supervisor_control.check_cohort_claim` — a PURE comparison of the
  plan's requested artist / assumed objection against the SELECTED leads' real
  attributes; a divergence yields the supervisor note (never a block).
* the copywriter SIGN-OFF IDENTITY gate — the prompt may authorize an artist
  sign-off ONLY when the operator explicitly set ``plan.artist`` or the lead's own
  record carries that artist; otherwise it hard-forbids signing as any artist, and
  the draft's grounding audit records which identity was allowed.

All pure — no DB, no model.
"""

from __future__ import annotations

from types import SimpleNamespace

from studio.customer_research import _build_email_prompt, build_outreach_draft
from studio.supervisor_control import check_cohort_claim, requested_artist_for_plan


def _plan(**kw):
    base = dict(artist="", goal="", campaign_type="", audience="", offer="",
                assumed_objection="")
    base.update(kw)
    return SimpleNamespace(**base)


def _lead(cid: str, artist=None):
    return {"customer_id": cid, "name": cid, "artist": artist}


# ── check_cohort_claim (pure) ─────────────────────────────────────────────── #


def test_keebs_winback_mismatch_produces_the_supervisor_note():
    """The exact harness scenario: plan claims a Keebs price winback; the selected
    leads have no Keebs history and non-price objections. The note must state both
    truths, in operator language."""
    plan = _plan(goal="Keebs price/timing winback", assumed_objection="price")
    cohort = [_lead("c1", "Maya"), _lead("c2", ""), _lead("c3", None)]
    objs = {"c1": "blocked_by_prereq", "c2": "blocked_by_prereq", "c3": "none-found"}
    note = check_cohort_claim(plan, cohort, objs, roster=["Keebs", "Maya"])
    assert note is not None
    assert note["rule"] == "cohort-claim-mismatch"
    assert "0 of 3 selected leads have a Keebs history" in note["detail"]
    assert "prerequisite (2)" in note["detail"]
    assert "unknown (1)" in note["detail"]  # none-found reads honestly as unknown
    assert "'price'" in note["detail"]
    assert note["question"] == "angle adjusted or proceed?"


def test_matching_cohort_yields_no_note():
    plan = _plan(artist="Keebs", assumed_objection="price")
    cohort = [_lead("c1", "Keebs"), _lead("c2", "keebs")]  # case-insensitive match
    objs = {"c1": "price", "c2": "none-found"}
    assert check_cohort_claim(plan, cohort, objs) is None


def test_partial_artist_match_still_surfaces_the_count():
    plan = _plan(artist="Keebs")
    cohort = [_lead("c1", "Keebs"), _lead("c2", "Maya"), _lead("c3", None)]
    note = check_cohort_claim(plan, cohort, {})
    assert note is not None
    assert "1 of 3 selected leads have a Keebs history" in note["detail"]


def test_plan_with_no_artist_or_objection_claim_never_notes():
    plan = _plan(goal="win back lapsed clients")
    assert check_cohort_claim(plan, [_lead("c1")], {}, roster=["Keebs"]) is None
    # And an empty cohort never fabricates a comparison.
    assert check_cohort_claim(_plan(artist="Keebs"), [], {}) is None


def test_requested_artist_explicit_setting_beats_goal_text():
    assert requested_artist_for_plan(
        _plan(artist="Maya", goal="Keebs winback"), ["Keebs", "Maya"]
    ) == ("Maya", True)
    # The implicit claim: a roster artist named in the goal free text.
    assert requested_artist_for_plan(
        _plan(goal="Keebs price/timing winback"), ["Keebs"]
    ) == ("Keebs", False)
    # No roster name in the text -> no artist claim (never a guess).
    assert requested_artist_for_plan(_plan(goal="general winback"), ["Keebs"]) == (
        None,
        False,
    )
    # A substring inside another word must not match ('Keebsy' is not 'Keebs'... and
    # 'art' inside 'partner' is not the artist 'Art').
    assert requested_artist_for_plan(_plan(goal="partner promo"), ["Art"]) == (None, False)


# ── the sign-off identity gate ────────────────────────────────────────────── #

_ANGLE = {"generic": True, "label": "", "key": "generic", "basis": "", "inferred": False}


def _facts(**kw):
    base = {
        "customer_id": "c1", "name": "Sam", "city": "", "notes": "",
        "persona_traits": {}, "interests": [], "tattoo_history": [], "memories": [],
    }
    base.update(kw)
    return base


def test_prompt_forbids_artist_signoff_for_unlinked_lead():
    prompt = _build_email_prompt(
        _facts(), goal="win back", research=[], angle=_ANGLE, artist_voice=None
    )
    assert "SIGN-OFF IDENTITY" in prompt
    assert "Do NOT sign as" in prompt
    assert "fabricate a relationship" in prompt


def test_prompt_authorizes_only_the_gated_artist():
    prompt = _build_email_prompt(
        _facts(), goal="win back", research=[], angle=_ANGLE, artist_voice="Keebs"
    )
    assert "sign this message as Keebs" in prompt
    assert "Never sign as any OTHER individual artist" in prompt


def test_grounding_audit_records_the_allowed_signoff_identity():
    # No plan.artist and no lead affinity -> studio sign-off, recorded in grounding.
    d = build_outreach_draft(_facts(), goal="hi", channel="instagram")
    assert "sign_off=studio" in d["grounding"]
    # Lead with a real artist affinity (or an operator-set artist) -> that artist.
    d2 = build_outreach_draft(
        _facts(artist="Keebs"), goal="hi", channel="instagram", artist_voice="Keebs"
    )
    assert "sign_off=artist:Keebs" in d2["grounding"]
