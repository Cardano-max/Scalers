"""Tests for the comment auto-reply ENGAGEMENT path (team-lead item #4, gated).

Hermetic by construction — no network, no DB, no LLM key:
  * triage classifies deterministically (keyword screens),
  * the reply generator is injected (a stub),
  * the decision store is in-memory and the jury panel runs through an injected
    deterministic ``judge_runner`` (FunctionModel-style),
  * the action recorder is a capturing stub (so we assert a PENDING action is
    recorded and that NOTHING sends).

The real-Postgres / real-jury exercise is the standalone simulate run in the bead's
verify step, not here.
"""

from __future__ import annotations

import pytest

from autonomy.decision import EscKind, SafetyVerdict
from autonomy.judges import JudgeScore, JudgeSpec
from autonomy.store import InMemoryDecisionStore
from engagement.ingest import IngestError, parse_comment_payload
from engagement.simulate import simulate_comment_event, synthesize_comment_event
from engagement.triage import (
    ReplyProposal,
    TriageCategory,
    classify_comment,
    make_default_reply_generator,
    triage_comment,
)
from harness.state import RouteDecision


# --------------------------------------------------------------------------- #
# triage classification (deterministic, no model)
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "text, expected",
    [
        ("do you have any spots left this month??", TriageCategory.QUESTION),
        ("How much for a small fine-line piece?", TriageCategory.QUESTION),
        ("I am OBSESSED with this, absolutely gorgeous 😍", TriageCategory.POSITIVE),
        ("thank you so much!!", TriageCategory.ROUTINE),
        ("Honestly I'm so disappointed, the linework came out uneven and I'm unhappy",
         TriageCategory.COMPLAINT),
        ("I think my tattoo is infected and swollen, what do I do??", TriageCategory.CRISIS),
        ("you are a scam, this is absolute garbage", TriageCategory.NEGATIVE),
        ("hmm", TriageCategory.AMBIGUOUS),
        ("", TriageCategory.AMBIGUOUS),
    ],
)
def test_classify_categories(text, expected):
    category, _ = classify_comment(text)
    assert category is expected


def test_classify_abuse_is_safety_veto():
    _, safety = classify_comment("you are a scam, this is absolute garbage")
    assert safety is SafetyVerdict.VETO


def test_classify_medical_crisis_is_safety_flag():
    cat, safety = classify_comment("my new tattoo looks infected, I want a refund")
    assert cat is TriageCategory.CRISIS
    assert safety is SafetyVerdict.FLAG


def test_triage_positive_is_not_escalated_and_drafts_reply():
    ev = synthesize_comment_event("instagram", "love your work, so beautiful!", "fan")
    res = triage_comment(ev, reply_generator=lambda e, c: ReplyProposal("hi", "stub"))
    assert res.category is TriageCategory.POSITIVE
    assert res.escalate is False
    assert res.escalation_reason is None
    assert res.reply == "hi"


def test_triage_complaint_sets_escalation_reason_and_still_drafts():
    ev = synthesize_comment_event(
        "facebook", "I'm really disappointed, the work was rushed and uneven", "jane"
    )
    res = triage_comment(ev, reply_generator=lambda e, c: ReplyProposal("holding draft", "stub"))
    assert res.category is TriageCategory.COMPLAINT
    assert res.escalate is True
    assert res.escalation_reason and "complaint" in res.escalation_reason.lower()
    assert res.reply  # an escalation is still drafted for the human


def test_default_reply_generator_falls_back_to_template_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    gen = make_default_reply_generator()
    ev = synthesize_comment_event("instagram", "do you have spots?", "ink_curious")
    prop = gen(ev, TriageCategory.QUESTION)
    assert prop.source == "template"  # clearly marked, never mistaken for an LLM draft
    assert "@ink_curious" in prop.text


# --------------------------------------------------------------------------- #
# ingest — IG and FB webhook shapes
# --------------------------------------------------------------------------- #

def test_parse_instagram_comment():
    payload = {
        "object": "instagram",
        "entry": [{
            "id": "17841400000000000",
            "changes": [{
                "field": "comments",
                "value": {
                    "id": "ig_comment_1",
                    "text": "do you have any spots left??",
                    "from": {"id": "55", "username": "ink_curious"},
                    "media": {"id": "media_99"},
                },
            }],
        }],
    }
    events = parse_comment_payload(payload)
    assert len(events) == 1
    e = events[0]
    assert e.platform == "instagram"
    assert e.comment_id == "ig_comment_1"
    assert e.author == "ink_curious"
    assert e.post_id == "media_99"
    assert "spots" in e.text


def test_parse_facebook_comment():
    payload = {
        "object": "page",
        "entry": [{
            "id": "page_1",
            "changes": [{
                "field": "feed",
                "value": {
                    "item": "comment",
                    "verb": "add",
                    "comment_id": "fb_comment_1",
                    "post_id": "page_1_post_7",
                    "message": "love this, booking soon!",
                    "from": {"id": "88", "name": "Jane Doe"},
                },
            }],
        }],
    }
    events = parse_comment_payload(payload)
    assert len(events) == 1
    e = events[0]
    assert e.platform == "facebook"
    assert e.comment_id == "fb_comment_1"
    assert e.author == "Jane Doe"
    assert e.post_id == "page_1_post_7"
    assert e.parent_id is None  # top-level comment


def test_parse_skips_non_comment_changes():
    fb_like = {
        "object": "page",
        "entry": [{"changes": [{"field": "feed", "value": {"item": "like", "verb": "add"}}]}],
    }
    ig_mention = {
        "object": "instagram",
        "entry": [{"changes": [{"field": "mentions", "value": {"media_id": "m"}}]}],
    }
    assert parse_comment_payload(fb_like) == []
    assert parse_comment_payload(ig_mention) == []


def test_parse_rejects_malformed_envelope():
    with pytest.raises(IngestError):
        parse_comment_payload({"object": "instagram"})  # no 'entry' list


# --------------------------------------------------------------------------- #
# handler — positive comment lands a PENDING reply action, NOTHING sends
# --------------------------------------------------------------------------- #

def _clean_runner():
    """A deterministic panel runner: every seat returns the same clean-high score."""
    score = JudgeScore(voice=0.95, safety=0.95, appr=0.95, on_voice=True)

    async def run(spec: JudgeSpec, action: str) -> JudgeScore:
        return score

    return run


def _capturing_recorder(sink: list[dict]):
    def record(**kwargs) -> str:
        sink.append(kwargs)
        return f"act_test_{len(sink)}"

    return record


def test_positive_comment_creates_pending_reply_action_no_send():
    recorded: list[dict] = []
    res = simulate_comment_event(
        "instagram", "I love this so much, gorgeous work! 😍", "fan",
        decision_store=InMemoryDecisionStore(),
        action_recorder=_capturing_recorder(recorded),
        reply_generator=lambda e, c: ReplyProposal("thank you! DM us to start your piece 💛", "stub"),
        judge_runner=_clean_runner(),
        self_consistency=1.0,
    )

    # exactly one PENDING reply action recorded, and NOTHING sent
    assert len(recorded) == 1
    rec = recorded[0]
    assert rec["type"] == "comment"
    assert rec["channel"] == "instagram"
    assert rec["worker"] == "Responder"
    assert rec["target"] == res.target
    assert rec["draft"] == "thank you! DM us to start your piece 💛"
    assert rec["decision_id"] == res.decision_id
    assert rec["idempotency_key"]  # exactly-once key derived
    assert res.sent is False
    assert res.status == "pending"

    # gated: HOLD forces review even on a clean-high cross-family jury
    assert res.routed == RouteDecision.REVIEW.value
    assert res.triage.category is TriageCategory.POSITIVE
    assert len(res.decision.jury) == 3
    assert {v.family for v in res.decision.jury} >= {"anthropic", "ollama"}


def test_complaint_comment_escalates_with_reason():
    recorded: list[dict] = []
    res = simulate_comment_event(
        "instagram", "honestly I'm so disappointed, the linework is uneven and I'm unhappy", "ex_client",
        decision_store=InMemoryDecisionStore(),
        action_recorder=_capturing_recorder(recorded),
        reply_generator=lambda e, c: ReplyProposal("a team member will follow up with you", "stub"),
        judge_runner=_clean_runner(),
        self_consistency=1.0,
    )
    assert res.triage.category is TriageCategory.COMPLAINT
    assert res.triage.escalate is True
    assert res.triage.escalation_reason and "complaint" in res.triage.escalation_reason.lower()
    assert res.routed == RouteDecision.REVIEW.value  # still gated, still drafted
    assert len(recorded) == 1  # the holding draft is queued for the human


def test_abusive_comment_routes_review_via_safety_veto():
    recorded: list[dict] = []
    res = simulate_comment_event(
        "facebook", "you are a scam, this is absolute garbage", "troll",
        decision_store=InMemoryDecisionStore(),
        action_recorder=_capturing_recorder(recorded),
        reply_generator=lambda e, c: ReplyProposal("neutral holding reply", "stub"),
        judge_runner=_clean_runner(),
        self_consistency=1.0,
    )
    assert res.triage.category is TriageCategory.NEGATIVE
    assert res.triage.safety_verdict is SafetyVerdict.VETO
    assert res.routed == RouteDecision.REVIEW.value
    assert res.decision.esc.kind is EscKind.SAFETY
    assert res.sent is False


def test_injected_reply_generator_is_used():
    recorded: list[dict] = []
    res = simulate_comment_event(
        "instagram", "do you have any spots left this month??", "ink_curious",
        decision_store=InMemoryDecisionStore(),
        action_recorder=_capturing_recorder(recorded),
        reply_generator=lambda e, c: ReplyProposal("INJECTED-DRAFT", "stub"),
        judge_runner=_clean_runner(),
        self_consistency=1.0,
    )
    assert res.triage.category is TriageCategory.QUESTION
    assert res.triage.reply == "INJECTED-DRAFT"
    assert recorded[0]["draft"] == "INJECTED-DRAFT"
