"""The engagement handler — comment event -> a GATED pending reply action.

``handle_comment_event`` is the seam that ties the slice together:

1. **Triage** the comment (classify + propose a draft + escalation reason).
2. Produce a **REAL cross-family decision** via
   :func:`~autonomy.produce.produce_and_record_decision_real` under
   ``autonomy=HOLD`` — the bead-439 safety hold, so the action can **never**
   auto-fire no matter the jury/confidence. The proposed reply text is what the
   jury scores; the comment's safety pre-screen feeds the decision's safety verdict.
3. Record a **PENDING** :mod:`actions` row (``type="comment"``) linked to that
   decision, so the console renders it in the review queue.

Nothing in this module sends. The connector that actually posts the approved reply
is wired separately (``actions.publish`` + the IG/FB connector reply method); here
``sent`` is always ``False``. The decision store + action recorder are injectable so
the path runs hermetically under test (in-memory store + a capturing recorder) and
against real Postgres in production.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from dataclasses import dataclass

from actions import store as actions_store
from autonomy.decision import DecisionRecord
from autonomy.judges import JudgeRunner
from autonomy.produce import produce_and_record_decision_real
from autonomy.store import DecisionStore, InMemoryDecisionStore, PostgresDecisionStore
from engagement.ingest import CommentEvent
from engagement.triage import ReplyGenerator, TriageResult, triage_comment
from harness.router import DEFAULT_THRESHOLD
from harness.state import AutonomyMode
from sideeffects.keys import idempotency_key

# The reply channel posts back to the same platform the comment came from; the
# engagement worker that owns comment replies (matches the seed/console naming).
_WORKER = "Responder"

# An action recorder: the seam that persists a PENDING action and returns its id.
# Defaults to actions.store.record_pending_action; tests inject a capturing stub.
ActionRecorder = Callable[..., str]


@dataclass(frozen=True)
class EngagementResult:
    """The outcome of handling one comment: the gated action + its trace."""

    action_id: str
    decision_id: str
    channel: str
    target: str
    triage: TriageResult
    decision: DecisionRecord
    sent: bool = False  # ALWAYS False here — the send is operator-gated, built elsewhere

    @property
    def routed(self) -> str:
        return self.decision.decision.value

    @property
    def status(self) -> str:
        return "pending"

    def __str__(self) -> str:  # pragma: no cover - presentation only
        t = self.triage
        return (
            "ENGAGEMENT comment -> GATED pending reply action (nothing sent)\n"
            f"  channel:       {self.channel}\n"
            f"  comment_id:    {self.target}\n"
            f"  triage:        {t.category.value} (escalate={t.escalate})\n"
            f"  escalation:    {t.escalation_reason or '-'}\n"
            f"  reply_source:  {t.reply_source}\n"
            f"  reply_draft:   {t.reply!r}\n"
            f"  decision_id:   {self.decision_id}\n"
            f"  autonomy:      HOLD  ->  routed={self.routed}\n"
            f"  confidence:    {self.decision.pooled_confidence:.2f} "
            f"(threshold {self.decision.threshold:.2f}, jurors {len(self.decision.jury)})\n"
            f"  escalation_chip: {self.decision.esc.kind.value} — {self.decision.esc.label}\n"
            f"  action_id:     {self.action_id}\n"
            f"  status:        {self.status}   sent: {self.sent}"
        )


def _clear_prior_decision(dsn: str, decision_id: str) -> None:
    """Drop any decision with this id (cascades its jury) so a re-run is idempotent.

    The Postgres decision store uses a plain INSERT (decision_id is the PK), so
    re-driving the same comment would otherwise hit a duplicate-key error. Mirrors
    the seed's idempotent re-seed pattern.
    """
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("DELETE FROM autonomy_decisions WHERE decision_id = %s", (decision_id,))


def _resolve_store(
    decision_store: DecisionStore | None, dsn: str | None
) -> tuple[DecisionStore, str | None]:
    """Resolve the decision store. If one is injected, use it (and signal no PG dsn,
    so no destructive clear runs). Otherwise build a Postgres store from ``dsn`` /
    ``ENGINE_DATABASE_URL`` when present (ensuring both schemas), else in-memory."""
    if decision_store is not None:
        return decision_store, None
    resolved = dsn or os.environ.get("ENGINE_DATABASE_URL")
    if resolved:
        store = PostgresDecisionStore(resolved)
        store.setup()
        actions_store.ensure_schema(resolved)
        return store, resolved
    return InMemoryDecisionStore(), None


def _build_context(event: CommentEvent, triage: TriageResult) -> str:
    """The console's 'replying to' context line, marked with draft provenance and any
    escalation reason so the operator sees WHY it is in the queue."""
    context = f'Replying to @{event.author}: "{event.text}"'
    marks = [f"draft:{triage.reply_source}"]
    if triage.escalate and triage.escalation_reason:
        marks.append(f"escalation:{triage.escalation_reason}")
    return f"{context}  [{' | '.join(marks)}]"


async def handle_comment_event(
    event: CommentEvent,
    *,
    tenant_id: str = "ladies8391",
    decision_store: DecisionStore | None = None,
    action_recorder: ActionRecorder | None = None,
    reply_generator: ReplyGenerator | None = None,
    judge_runner: JudgeRunner | None = None,
    self_consistency: float | None = None,
    brand_voice_context: str = "",
    approved_claims: tuple[str, ...] = (),
    threshold: float = DEFAULT_THRESHOLD,
    dsn: str | None = None,
    run_id: str | None = None,
) -> EngagementResult:
    """Handle one inbound comment: triage -> HOLD decision -> PENDING reply action.

    Returns the :class:`EngagementResult` (the gated action + its decision trace).
    No reply is sent — the action lands ``status='pending'`` for operator approval.
    """
    triage = triage_comment(
        event,
        reply_generator=reply_generator,
        brand_voice_context=brand_voice_context,
        approved_claims=approved_claims,
    )

    run_id = run_id or f"engagement-{tenant_id}"
    decision_id = f"{run_id}-{event.comment_id}"

    store, pg_dsn = _resolve_store(decision_store, dsn)
    if pg_dsn is not None:
        _clear_prior_decision(pg_dsn, decision_id)

    # The REAL cross-family producer under HOLD. The jury scores the proposed reply;
    # HOLD forces review regardless of any jury/confidence outcome (the action can
    # never auto-fire). With no judge models configured the panel is empty and the
    # decision fails safe to review — honest, never a fabricated green.
    record = await produce_and_record_decision_real(
        store,
        decision_id=decision_id,
        run_id=run_id,
        tenant_id=tenant_id,
        channel=event.platform,
        action_kind="comment",
        action=triage.reply,
        threshold=threshold,
        autonomy=AutonomyMode.HOLD,
        safety_verdict=triage.safety_verdict,
        judge_runner=judge_runner,
        self_consistency=self_consistency,
    )

    recorder = action_recorder or actions_store.record_pending_action
    idem = idempotency_key(tenant_id, event.platform, event.comment_id, triage.reply)
    action_id = recorder(
        tenant_id=tenant_id,
        decision_id=decision_id,
        type="comment",
        channel=event.platform,
        worker=_WORKER,
        target=event.comment_id,
        draft=triage.reply,
        subject=None,
        context=_build_context(event, triage),
        conf=record.pooled_confidence,
        threshold=record.threshold,
        esc_kind=record.esc.kind.value,
        esc_label=record.esc.label,
        idempotency_key=idem,
        run_id=run_id,
        dsn=pg_dsn,
    )

    return EngagementResult(
        action_id=action_id,
        decision_id=decision_id,
        channel=event.platform,
        target=event.comment_id,
        triage=triage,
        decision=record,
        sent=False,
    )


def handle_comment_event_sync(event: CommentEvent, **kwargs) -> EngagementResult:
    """Synchronous wrapper around :func:`handle_comment_event` (drives the async
    producer with ``asyncio.run`` for scripts / the simulate helper)."""
    return asyncio.run(handle_comment_event(event, **kwargs))
