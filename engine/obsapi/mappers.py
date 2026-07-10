"""DB-value → contract-value mappers.

The console (``web/lib/data/models.ts``) uses UPPERCASE string unions for enums
(``GMAIL``, ``OUTREACH``, ``SUCCESS`` …) while Postgres stores lowercase. These
helpers map known values to the contract form and uppercase anything unexpected,
so the API never crashes on a value outside the union (it degrades to a plausible
string instead). They return plain ``str`` — the GraphQL response is JSON either
way; the console's TypeScript unions are compile-time only.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

_CHANNEL = {"gmail": "GMAIL", "instagram": "INSTAGRAM", "facebook": "FACEBOOK"}
_TYPE = {"outreach": "OUTREACH", "comment": "COMMENT", "dm": "DM", "post": "POST"}
_WORKER = {
    "outreach": "OUTREACH",
    "responder": "RESPONDER",
    "publisher": "PUBLISHER",
    "jury": "JURY",
    "classifier": "CLASSIFIER",
    "safety": "SAFETY",
    "mailbox_mcp": "MAILBOX_MCP",
    "meta_mcp": "META_MCP",
    "webhook": "WEBHOOK",
    "temporal": "TEMPORAL",
    "research": "RESEARCH",
    "strategist": "STRATEGIST",
    "copywriter": "COPYWRITER",
}
_WORKER_BY_TYPE = {
    "outreach": "OUTREACH",
    "comment": "RESPONDER",
    "dm": "RESPONDER",
    "post": "PUBLISHER",
}
# EscalationKind union: CONFIDENCE | SAFETY | SPLIT | GATE | INTENT | WEAK_PERSONALIZATION
_ESC = {
    "confidence": "CONFIDENCE",
    "below_threshold": "CONFIDENCE",
    "safety": "SAFETY",
    "split": "SPLIT",
    "degraded": "SPLIT",
    "gate": "GATE",
    "mode": "GATE",
    "held": "GATE",
    "media": "GATE",
    "intent": "INTENT",
    "weak_personalization": "WEAK_PERSONALIZATION",
    "none": "NONE",
}
_RUN_TRIGGER = {
    "manual": "COMMAND",
    "command": "COMMAND",
    "schedule": "SCHEDULE",
    "event": "EVENT",
}
_RUN_STATUS = {
    "completed": "SUCCESS",
    "success": "SUCCESS",
    "failed": "FAILED",
    "running": "RUNNING",
    "needs-review": "RUNNING",
}


def channel(v: str | None) -> str:
    return _CHANNEL.get((v or "").lower(), (v or "").upper())


def action_type(v: str | None) -> str:
    return _TYPE.get((v or "").lower(), (v or "").upper())


def worker(v: str | None, type_: str | None = None) -> str:
    if v:
        return _WORKER.get(v.lower(), v.upper())
    return _WORKER_BY_TYPE.get((type_ or "").lower(), "OUTREACH")


def status(v: str | None) -> str:
    return (v or "").upper()


def activity_autonomy(v: str | None) -> str:
    """``actions.autonomy`` ('auto' | 'approved') → contract form. Auto-fired vs
    operator-approved; uppercased like the other enum-ish fields."""

    return {"auto": "AUTO", "approved": "APPROVED"}.get(
        (v or "").lower(), (v or "").upper()
    )


def esc_kind(v: str | None) -> str:
    return _ESC.get((v or "").lower(), (v or "").upper() or "NONE")


def run_trigger(v: str | None) -> str:
    return _RUN_TRIGGER.get((v or "").lower(), (v or "").upper())


def run_status(v: str | None) -> str:
    return _RUN_STATUS.get((v or "").lower(), (v or "").upper())


def agreement(value: float | None) -> str:
    """Render the numeric jury-agreement fraction as the console's string label."""

    if value is None:
        return ""
    if value >= 0.999:
        return "unanimous"
    if value >= 0.66:
        return "majority"
    return "split"


def step_state(value: str | None) -> str:
    s = (value or "").lower()
    if s in ("ok", "done", "success"):
        return "done"
    if s in ("failed", "error"):
        return "error"
    if s in ("warn", "warning"):
        return "warn"
    return s or "done"


def iso(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _fmt_secs(secs: float) -> str:
    if secs < 60:
        return f"{secs:.1f}s"
    mins, rem = divmod(int(secs), 60)
    return f"{mins}m {rem}s"


def duration(created: Any, updated: Any) -> str | None:
    if not isinstance(created, datetime) or not isinstance(updated, datetime):
        return None
    secs = (updated - created).total_seconds()
    if secs < 0:
        return None
    return _fmt_secs(secs)


# A span shorter than this is indistinguishable from a single-write timestamp pair
# (created_at ≈ updated_at), so it is NOT treated as a real measured duration.
_MIN_REAL_SPAN_SECONDS = 1.0


def run_duration(
    created: Any, updated: Any, first_step: Any, last_step: Any
) -> str | None:
    """The HONEST run duration for the runs listing/detail.

    Studio campaign runs materialize their ``runs`` row in ONE write at completion,
    so ``created_at ≈ updated_at`` there — a "0.0s" derived from that pair is a
    fabricated duration over 30-60s of real work. The real signal for those runs is
    the run's own step span: ``min(created_at)..max(created_at)`` of its
    ``agent_runs``. Preference order:

      * the runs-row span, when it is meaningfully positive (a row genuinely opened
        at start and finished at end);
      * else the agent_runs step span, when the run has 2+ timestamped steps;
      * else ``None`` — an honest unknown, NEVER a fake ``0.0s``.
    """
    for lo, hi in ((created, updated), (first_step, last_step)):
        if isinstance(lo, datetime) and isinstance(hi, datetime):
            secs = (hi - lo).total_seconds()
            if secs >= _MIN_REAL_SPAN_SECONDS:
                return _fmt_secs(secs)
    return None
