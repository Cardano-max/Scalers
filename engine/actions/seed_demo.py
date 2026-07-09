"""Seed realistic PENDING review-queue actions for the live console demo.

Creates a handful of brand-appropriate, PENDING actions for the **Ladies First**
tattoo studio (``@ladies8391``) — a cold-outreach email, an Instagram post
caption, a Facebook post, and a comment auto-reply — each linked to a REAL
``autonomy_decisions`` row (+ its cross-family ``autonomy_jury`` rows) so the
console's jury card, confidence bar, and escalation chip render from real data.

This **seeds PENDING rows only**. It performs NO send and touches NO Gmail/Meta
credentials — the real send happens later, only when the operator approves an
action and :func:`actions.publish.approve_and_publish` runs. Re-running the seed
is idempotent (stable decision ids + a UNIQUE ``idempotency_key`` per action).
"""

from __future__ import annotations

import os

from actions import store as actions_store
from autonomy.decision import (
    DecisionRecord,
    GateResult,
    JudgeVote,
    SafetyVerdict,
    derive_decision,
)
from autonomy.jury import JURY_PANEL
from autonomy.store import PostgresDecisionStore
from harness.router import DEFAULT_THRESHOLD
from harness.state import AutonomyMode, Gate
from sideeffects.keys import idempotency_key


def _dsn(dsn: str | None) -> str:
    return dsn or os.environ.get("ENGINE_DATABASE_URL") or actions_store._DEFAULT_DSN


def _votes(scores: list[tuple[float, float, float]]) -> list[JudgeVote]:
    """Build one cross-family JudgeVote per (voice, safety, appr) tuple, paired
    with the canonical cross-family panel (so the jury card is auditable)."""
    return [
        JudgeVote(judge=judge, family=family, voice=v, safety=s, appr=a)
        for (judge, family), (v, s, a) in zip(JURY_PANEL, scores)
    ]


def _record_decision(
    store: PostgresDecisionStore,
    *,
    dsn: str,
    decision_id: str,
    run_id: str,
    tenant_id: str,
    channel: str,
    action_kind: str,
    votes: list[JudgeVote],
    threshold: float,
    autonomy: AutonomyMode,
    safety_verdict: SafetyVerdict,
    gates: list[Gate],
) -> DecisionRecord:
    """Persist one realistic REVIEW decision + its jury rows (idempotent: a prior
    decision with this id is replaced, cascading to its jury rows)."""
    decision, esc, pooled, agree = derive_decision(
        votes=votes,
        threshold=threshold,
        gates=gates,
        autonomy=autonomy,
        safety_verdict=safety_verdict,
        expected_judges=len(votes),
    )
    record = DecisionRecord(
        decision_id=decision_id,
        run_id=run_id,
        tenant_id=tenant_id,
        channel=channel,
        action_kind=action_kind,
        jury=votes,
        pooled_confidence=pooled,
        threshold=threshold,
        agreement=agree,
        gates=[GateResult.from_gate(g) for g in gates],
        safety_verdict=safety_verdict,
        decision=decision,
        esc=esc,
    )
    # Idempotent re-seed: drop any prior decision with this id (cascades jury).
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("DELETE FROM autonomy_decisions WHERE decision_id = %s", (decision_id,))
    store.record_decision(record)
    return record


def seed_demo_actions(
    tenant_id: str = "ladies8391",
    *,
    dsn: str | None = None,
    outreach_to: str | None = None,
) -> list[str]:
    """Seed the demo PENDING actions; return the list of action ids.

    ``outreach_to`` is the cold-outreach email recipient (defaults to
    ``DEMO_OUTREACH_TO`` in the environment, else a placeholder prospect address —
    set it to a real inbox before the live send so the demo email is deliverable).
    No send happens here.
    """
    dsn = _dsn(dsn)
    outreach_to = outreach_to or os.environ.get("DEMO_OUTREACH_TO", "owner@studio-prospect.example")

    # Ensure both schemas exist (decisions + jury, and the actions table).
    decision_store = PostgresDecisionStore(dsn)
    decision_store.setup()
    actions_store.ensure_schema(dsn)

    run_id = f"demo-{tenant_id}"
    brand = "Ladies First"
    handle = "@ladies8391"

    # Each spec: a realistic, brand-appropriate action + a real REVIEW decision.
    specs = [
        {
            "slug": "outreach-email",
            "type": "outreach",
            "channel": "gmail",
            "worker": "Outreach",
            "target": outreach_to,
            "subject": f"Your custom piece at {brand} — let's find your chair",
            "context": None,
            "draft": (
                f"Hi there,\n\n"
                f"This is the team at {brand} ({handle}). You reached out about a "
                f"custom fine-line piece, and we'd love to bring it to life with you.\n\n"
                f"Our women-led studio specializes in delicate blackwork and "
                f"botanical linework, and we keep a few consult slots open each week "
                f"so the design never feels rushed. If you're still thinking it over, "
                f"we can hold a spot this month and sketch a first concept — no "
                f"commitment until you love it.\n\n"
                f"Reply here and we'll find a time that works.\n\n"
                f"Warmly,\nThe {brand} team"
            ),
            # below_threshold: confident but just under the auto bar -> human review.
            "votes": _votes([(0.85, 0.84, 0.80), (0.83, 0.86, 0.78),
                             (0.84, 0.85, 0.79), (0.82, 0.83, 0.81)]),
            "threshold": 0.85,
            "autonomy": AutonomyMode.REVIEW,
            "safety": SafetyVerdict.PASS,
            "gates": [Gate(name="suppression", passed=True), Gate(name="spam-words", passed=True)],
        },
        {
            "slug": "ig-post",
            "type": "post",
            "channel": "instagram",
            "worker": "Publisher",
            "target": handle,
            "subject": None,
            "context": None,
            "draft": (
                "Fine-line season is here 🌿 Booking spring chairs now for custom "
                "botanical + blackwork pieces. Swipe for healed work from the studio, "
                "then DM us your idea — we'll sketch the first concept together. "
                "Women-led, by appointment. #finelinetattoo #blackwork #ladiesfirst"
            ),
            # channel set to approve-first (mode): high confidence, still human-gated.
            "votes": _votes([(0.92, 0.93, 0.90), (0.91, 0.92, 0.89),
                             (0.93, 0.91, 0.92), (0.90, 0.92, 0.90)]),
            "threshold": DEFAULT_THRESHOLD,
            "autonomy": AutonomyMode.REVIEW,
            "safety": SafetyVerdict.PASS,
            "gates": [Gate(name="media-aspect", passed=True), Gate(name="banned-phrase", passed=True)],
        },
        {
            "slug": "fb-post",
            "type": "post",
            "channel": "facebook",
            "worker": "Publisher",
            "target": brand,
            "subject": None,
            "context": None,
            "draft": (
                f"This Saturday at {brand}: walk-in flash day. A sheet of small "
                f"fine-line and blackwork designs, first-come first-served, 12–6pm. "
                f"Bring a friend — our chairs (and our playlist) are ready. "
                f"Comment 'FLASH' and we'll send you the sheet."
            ),
            # split jury: jurors disagree enough that auto-fire isn't safe.
            "votes": _votes([(0.96, 0.95, 0.94), (0.88, 0.90, 0.86),
                             (0.70, 0.72, 0.68), (0.45, 0.42, 0.40)]),
            "threshold": DEFAULT_THRESHOLD,
            "autonomy": AutonomyMode.REVIEW,
            "safety": SafetyVerdict.PASS,
            "gates": [Gate(name="media-aspect", passed=True), Gate(name="banned-phrase", passed=True)],
        },
        {
            "slug": "comment-reply",
            "type": "comment",
            "channel": "instagram",
            "worker": "Responder",
            "target": "ig_comment:17900000000111222",
            "subject": None,
            "context": "Replying to @ink_curious: \"Do you have any spots left this month?? 😍\"",
            "draft": (
                "@ink_curious yes! We just had a spring slot open up 🌸 DM us your "
                "idea and rough size and we'll get you on the books. Can't wait to "
                "design something with you!"
            ),
            # safety FLAG: an auto-reply is held for a human safety check.
            "votes": _votes([(0.89, 0.88, 0.87), (0.88, 0.87, 0.86),
                             (0.90, 0.89, 0.88), (0.87, 0.88, 0.86)]),
            "threshold": DEFAULT_THRESHOLD,
            "autonomy": AutonomyMode.REVIEW,
            "safety": SafetyVerdict.FLAG,
            "gates": [Gate(name="banned-phrase", passed=True)],
        },
    ]

    action_ids: list[str] = []
    for spec in specs:
        decision_id = f"{run_id}-{spec['slug']}"
        record = _record_decision(
            decision_store,
            dsn=dsn,
            decision_id=decision_id,
            run_id=run_id,
            tenant_id=tenant_id,
            channel=spec["channel"],
            action_kind=spec["type"],
            votes=spec["votes"],
            threshold=spec["threshold"],
            autonomy=spec["autonomy"],
            safety_verdict=spec["safety"],
            gates=spec["gates"],
        )
        idem = idempotency_key(tenant_id, spec["channel"], spec["target"] or "", spec["draft"])
        action_id = actions_store.record_pending_action(
            tenant_id=tenant_id,
            decision_id=decision_id,
            type=spec["type"],
            channel=spec["channel"],
            worker=spec["worker"],
            target=spec["target"],
            draft=spec["draft"],
            subject=spec["subject"],
            context=spec["context"],
            conf=record.pooled_confidence,
            threshold=record.threshold,
            esc_kind=record.esc.kind.value,
            esc_label=record.esc.label,
            idempotency_key=idem,
            run_id=run_id,
            dsn=dsn,
            is_seeded=True,  # Slice-5: PERSIST the seed marker so the console
            # badges these rows and they can never masquerade as a live action.
        )
        action_ids.append(action_id)

    return action_ids
