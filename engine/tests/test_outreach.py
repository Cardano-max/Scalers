"""Outreach engine tests (bead 1mk.7) — DB-free, hermetic (no network, no send).

Proves the AC + edge cases: suppression-first skip, deliverability block of
risky/unverified, capped 4-touch spacing, reply/bounce hard-stop, no creepy
over-personalization, RFC-8058 unsubscribe on every touch, and the 439 hold
(nothing auto-sends; plans route to review; no raw email in the plan).
"""

from __future__ import annotations

from outreach import (
    Disposition,
    DeliverabilityVerifier,
    OutreachPolicy,
    Prospect,
    SequencePlanner,
    SuppressionGate,
    cap_per_inbox_day,
    prospect_ref,
    screen_signals,
    verify_email,
)
from outreach.schema import TOUCH_DAY_OFFSETS


def _policy(**kw) -> OutreachPolicy:
    return OutreachPolicy(**kw)


# ── suppression-first ────────────────────────────────────────────────────────


def test_suppression_first_skips_before_anything_else():
    gate = SuppressionGate(emails=["nope@studio.com"])
    plan = _policy(suppression=gate).plan(Prospect(email="nope@studio.com"))
    assert plan.disposition is Disposition.SKIP_SUPPRESSED
    assert plan.sequence is None
    # suppression is checked before verification (no verdict computed)
    assert plan.verification is None


def test_suppression_by_domain():
    gate = SuppressionGate(domains=["@competitor.com"])
    assert gate.is_suppressed("anyone@competitor.com")
    plan = _policy(suppression=gate).plan(Prospect(email="anyone@competitor.com"))
    assert plan.disposition is Disposition.SKIP_SUPPRESSED


# ── deliverability verification ──────────────────────────────────────────────


def test_undeliverable_is_blocked():
    plan = _policy().plan(Prospect(email="not-an-email"))
    assert plan.disposition is Disposition.BLOCK_UNDELIVERABLE
    assert plan.sequence is None


def test_disposable_domain_blocked():
    assert verify_email("a@mailinator.com").status == "undeliverable"


def test_role_account_is_risky_and_escalates_with_warning():
    plan = _policy().plan(Prospect(email="info@inkstudio.com"))
    assert plan.disposition is Disposition.ESCALATE       # not auto, not blocked
    assert plan.verification.status == "risky"
    assert any("risky" in w for w in plan.warnings)


def test_mx_seam_can_downgrade_to_undeliverable():
    v = DeliverabilityVerifier(mx_check=lambda domain: False)
    assert v.verify("real@good-shape.com").status == "undeliverable"


# ── capped, spaced sequence ──────────────────────────────────────────────────


def test_sequence_is_four_touches_at_spec_offsets():
    plan = _policy().plan(Prospect(email="client@example.com"))
    assert plan.disposition is Disposition.ESCALATE
    seq = plan.sequence
    assert seq.length == 4
    assert tuple(t.day_offset for t in seq.touches) == TOUCH_DAY_OFFSETS
    assert all(t.includes_unsubscribe for t in seq.touches)  # RFC 8058 every touch


def test_warmup_ramp_caps():
    assert cap_per_inbox_day(1) == 8
    assert cap_per_inbox_day(4) == 40
    assert cap_per_inbox_day(9) == 40            # steady (Workspace)
    assert cap_per_inbox_day(9, consumer=True) == 25


# ── reply / bounce hard-stop ─────────────────────────────────────────────────


def test_reply_hard_stops_sequence():
    plan = _policy().plan(Prospect(email="client@example.com"), events=("reply",))
    assert plan.disposition is Disposition.HARD_STOP
    assert plan.sequence is None


def test_bounce_and_unsubscribe_hard_stop():
    for ev in ("bounce", "unsubscribe", "spam_complaint"):
        plan = _policy().plan(Prospect(email="client@example.com"), events=(ev,))
        assert plan.disposition is Disposition.HARD_STOP


def test_planner_stop_index():
    p = SequencePlanner()
    assert p.stop_index(("reply",)) == 0
    assert p.stop_index(()) is None


# ── over-personalization (creepy) guard ──────────────────────────────────────


def test_creepy_signals_stripped_and_flagged():
    g = screen_signals(("loves fine-line flash", "recently went through a divorce"))
    assert "loves fine-line flash" in g.allowed
    assert any("divorce" in b for b in g.blocked)
    assert g.warnings


def test_creepy_personalization_does_not_reach_touch_briefs():
    plan = _policy().plan(
        Prospect(email="client@example.com",
                 signals=("asked about blackwork", "lives at 12 Main St"))
    )
    briefs = [b for t in plan.sequence.touches for b in t.personalization_brief]
    assert all("Main St" not in b for b in briefs)
    assert any("creepy personalization blocked" in w for w in plan.warnings)


def test_refs_per_touch_capped():
    plan = _policy().plan(
        Prospect(email="client@example.com",
                 signals=("a", "b", "c", "d", "e"))
    )
    assert all(len(t.personalization_brief) <= 2 for t in plan.sequence.touches)


# ── safety posture: 439 hold + PII discipline ────────────────────────────────


def test_nothing_auto_sends_under_439():
    plan = _policy().plan(Prospect(email="client@example.com"))
    assert plan.routed_to == "review"
    assert plan.will_send is False


def test_plan_is_pii_free():
    email = "person@example.com"
    plan = _policy().plan(Prospect(email=email))
    assert plan.prospect_ref == prospect_ref(email)
    # the raw email must never appear in the auditable plan
    assert email not in repr(plan)


# ── shared handoff vocab with the reply engine (1mk.6) ───────────────────────


def test_escalation_uses_shared_vocab():
    from autonomy.decision import EscKind
    from harness.state import RouteDecision

    # clean prospect -> escalate via the channel dial (MODE), route=REVIEW
    clean = _policy().plan(Prospect(email="client@example.com"))
    assert clean.route is RouteDecision.REVIEW
    assert clean.escalation.kind is EscKind.MODE

    # deterministic gates (suppression / deliverability / hard-stop) -> GATE
    supp = _policy(suppression=SuppressionGate(emails=["x@y.com"])).plan(Prospect(email="x@y.com"))
    assert supp.escalation.kind is EscKind.GATE
    block = _policy().plan(Prospect(email="bad"))
    assert block.escalation.kind is EscKind.GATE
    stop = _policy().plan(Prospect(email="client@example.com"), events=("bounce",))
    assert stop.escalation.kind is EscKind.GATE
