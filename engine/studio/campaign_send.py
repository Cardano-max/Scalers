"""Campaign-level SAFE send — operator-initiated, gated, and exactly-once.

This is the campaign-level wrapper the console's "Send eligible / safe" control
calls. It does NOT introduce a new send path: every actual send goes through the
EXISTING per-draft :func:`actions.publish.approve_and_publish`, so it keeps that
path's guarantees verbatim:

  * the atomic ``pending -> sending`` claim (exactly-once: a draft already
    claimed/sent is never re-sent), and
  * the gmail allow-list / redirect (a draft whose ``worker`` is NOT
    ``'studio_real_send'`` is redirected to the operator inbox when
    ``GMAIL_REDIRECT_TO`` is set, so a campaign batch can never blast real
    strangers).

There is deliberately NO bulk-send that bypasses those. "Send eligible" simply
iterates the eligible drafts and calls ``approve_and_publish`` for each one.

Eligibility is FAIL-CLOSED. A draft is "eligible / safe" only when it has a
COMPUTED confidence at or above its threshold AND carries no safety / gate / split /
media / below-confidence escalation. A draft with no computed confidence (e.g. the
per-lead outreach drafts staged ``approval_required`` with ``conf=None``) is NOT
eligible — it goes to "review required". The ONLY way a non-eligible draft reaches
the send path is :func:`override_send`, which requires an explicit reason and writes
a :mod:`actions.audit` record first.

Nothing here auto-approves: the operator clicks "Send eligible" (HELD / approve-first
holds — this is the operator's approval, applied per-draft through the same path).
"""

from __future__ import annotations

import re
from typing import Any

# Escalation kinds that BLOCK a draft from the safe batch — a real safety / gate /
# jury-quality problem the operator must look at individually. ``hold`` (the plain
# approve-first hold every Phase-A draft carries) is NOT in this set; it is the
# normal held state, not a defect.
BLOCKING_ESC_KINDS = frozenset({
    "safety", "gate", "split", "media", "confidence", "degraded",
})

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def eligibility(action: Any) -> tuple[bool, str]:
    """``(eligible, reason)`` for one staged draft. Fail-closed: anything we cannot
    positively clear is NOT eligible (it routes to review)."""
    status = (getattr(action, "status", "") or "").lower()
    if status != "pending":
        return False, f"not pending (status={status or 'unknown'})"
    conf = getattr(action, "conf", None)
    threshold = getattr(action, "threshold", None)
    if conf is None or threshold is None:
        return False, "no computed confidence (approval required)"
    if conf < threshold:
        return False, f"below confidence bar ({conf:.2f} < {threshold:.2f})"
    esc = (getattr(action, "esc_kind", "") or "").lower()
    if esc in BLOCKING_ESC_KINDS:
        return False, f"escalation: {esc}"
    # Recipient validity (defense-in-depth): an email draft must have a real address.
    channel = (getattr(action, "channel", "") or "").lower()
    if channel in ("gmail", "email"):
        target = (getattr(action, "target", "") or "").strip()
        if not _EMAIL_RE.match(target):
            return False, "recipient address is missing or invalid"
    return True, f"confidence {conf:.2f} >= {threshold:.2f}"


def _summary(action: Any, *, eligible: bool, reason: str) -> dict[str, Any]:
    return {
        "action_id": action.id,
        "run_id": getattr(action, "run_id", None),
        "channel": getattr(action, "channel", None),
        "target": getattr(action, "target", None),
        "worker": getattr(action, "worker", None),
        "conf": getattr(action, "conf", None),
        "threshold": getattr(action, "threshold", None),
        "esc_kind": getattr(action, "esc_kind", None),
        "eligible": eligible,
        "reason": reason,
    }


def _pending_actions(
    *, run_id: str | None, tenant_id: str | None, dsn: str | None
) -> list[Any]:
    from actions.store import list_actions, list_actions_for_run

    if run_id:
        return list_actions_for_run(run_id, status="pending", dsn=dsn)
    if tenant_id:
        return list_actions(tenant_id, status="pending", dsn=dsn)
    raise ValueError("classify_campaign needs a run_id or a tenant_id")


def classify_campaign(
    *, run_id: str | None = None, tenant_id: str | None = None, dsn: str | None = None
) -> dict[str, Any]:
    """READ-ONLY split of a campaign's PENDING drafts into ``eligible`` (safe to
    batch-send) and ``review_required`` (must be looked at individually). Sends
    nothing."""
    actions = _pending_actions(run_id=run_id, tenant_id=tenant_id, dsn=dsn)
    eligible: list[dict[str, Any]] = []
    review: list[dict[str, Any]] = []
    for a in actions:
        ok, reason = eligibility(a)
        (eligible if ok else review).append(_summary(a, eligible=ok, reason=reason))
    return {
        "run_id": run_id,
        "tenant_id": tenant_id,
        "eligible": eligible,
        "review_required": review,
        "n_eligible": len(eligible),
        "n_review_required": len(review),
    }


def send_eligible(
    *,
    run_id: str | None = None,
    tenant_id: str | None = None,
    dsn: str | None = None,
    connectors: dict[str, Any] | None = None,
    operator: str | None = None,
) -> dict[str, Any]:
    """Operator-initiated: send ONLY the eligible/safe drafts of a campaign, each
    through the existing :func:`actions.publish.approve_and_publish` (atomic
    exactly-once claim + gmail allow-list/redirect). Non-eligible drafts are NOT
    sent — they are returned under ``skipped`` for review. Writes a ``send_eligible``
    audit row per draft sent."""
    from actions.audit import record_send_audit
    from actions.publish import approve_and_publish

    actions = _pending_actions(run_id=run_id, tenant_id=tenant_id, dsn=dsn)
    sent: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for a in actions:
        ok, reason = eligibility(a)
        if not ok:
            skipped.append(_summary(a, eligible=False, reason=reason))
            continue
        # SAME approve path — exactly-once claim + allow-list redirect preserved.
        row = approve_and_publish(a.id, connectors=connectors, dsn=dsn)
        status = getattr(row, "status", None)
        try:
            record_send_audit(
                action_id=a.id, kind="send_eligible", run_id=getattr(a, "run_id", None),
                tenant_id=getattr(a, "tenant_id", None), operator=operator, reason=None,
                eligible=True, conf=getattr(a, "conf", None),
                threshold=getattr(a, "threshold", None), esc_kind=getattr(a, "esc_kind", None),
                result=status, dsn=dsn,
            )
        except Exception:
            pass  # auditing must never break the (already-completed) send record
        entry = _summary(a, eligible=True, reason=reason)
        entry["result"] = status
        entry["last_error"] = getattr(row, "last_error", None)
        (sent if status == "sent" else failed).append(entry)

    return {
        "run_id": run_id,
        "tenant_id": tenant_id,
        "operator": operator,
        "sent": sent,
        "failed": failed,
        "skipped": skipped,
        "n_sent": len(sent),
        "n_failed": len(failed),
        "n_skipped": len(skipped),
    }


class OverrideRequiresReasonError(ValueError):
    """``override_send`` was called without an explicit reason. The override of a
    non-eligible draft must be justified and audited — never a bare force-send."""


def override_send(
    action_id: str,
    *,
    reason: str,
    operator: str | None = None,
    dsn: str | None = None,
    connectors: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """OVERRIDE one specific draft past the eligibility gate. This is the ONLY way a
    below-bar / flagged draft reaches the send path. It REQUIRES an explicit
    ``reason``, writes an ``override`` :mod:`actions.audit` row BEFORE sending, and
    then routes the send through the SAME :func:`approve_and_publish` (exactly-once
    + allow-list still apply — override bypasses our eligibility heuristic, never the
    real send-path safety)."""
    from actions.audit import record_send_audit
    from actions.publish import ActionNotFoundError, approve_and_publish
    from actions.store import get_action

    if not reason or not reason.strip():
        raise OverrideRequiresReasonError("override requires a non-empty reason")

    action = get_action(action_id, dsn=dsn)
    if action is None:
        raise ActionNotFoundError(action_id)

    ok, why = eligibility(action)
    # Audit the override BEFORE the send so the intent is durable even if the send
    # then fails or the process crashes.
    record_send_audit(
        action_id=action_id, kind="override", run_id=getattr(action, "run_id", None),
        tenant_id=getattr(action, "tenant_id", None), operator=operator,
        reason=reason.strip(), eligible=ok, conf=getattr(action, "conf", None),
        threshold=getattr(action, "threshold", None), esc_kind=getattr(action, "esc_kind", None),
        result=None, dsn=dsn,
    )
    row = approve_and_publish(action_id, connectors=connectors, dsn=dsn)
    return {
        "action_id": action_id,
        "was_eligible": ok,
        "eligibility_reason": why,
        "result": getattr(row, "status", None),
        "last_error": getattr(row, "last_error", None),
        "operator": operator,
        "reason": reason.strip(),
    }
