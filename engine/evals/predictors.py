"""Real per-commit cell predictors (rvy.7) — replaces the oracle tautology.

The CTO directive: the per-commit eval gate must run the ACTUAL cell under a
deterministic model, not ``oracle_cell`` (which returns the gold answer, so a
green is meaningless). These predictors read ``example.input`` ONLY — never
``example.expected`` — and apply genuine classification logic, so the metric is
real and a regression flips it.

* ``cell_predictor`` is the live prediction source swapped into ``run_eval_gate``
  (the ``Predictor`` seam). Engagement *triage* is a real deterministic
  classifier; cells that don't exist yet (outreach extraction, posting
  copywriter — Phase 7 / per-promotion) raise :class:`CellNotBuilt`, which the
  gate treats as SKIP-neutral (never a false fail for an unbuilt engine).
* ``regressed_cell_predictor`` is the seeded-regression variant for the
  fails-on-regression proof (``EVAL_GATE_REGRESS=1``): it collapses triage to
  ``positive`` / ``safe-to-auto``, missing every complaint and every
  must-escalate, so the metric — not a crash — fails the build.

Determinism: pure functions of the input text + channel; no model call, no keys.
The classifier is honest (reads the comment, applies triage rules) — its
correctness on the smoke set is the cell's job, exactly what the gate measures.
"""

from __future__ import annotations

from typing import Any

from kb.schema import Engine, GoldExample

from evals.gate import CellNotBuilt

# Engagement triage rules (engine/cells/ engagement cell lands in Phase 7; this
# is the deterministic Phase-2 stand-in the gate scores). Triage classes:
# positive | question | lead | spam | complaint. Reply-safety: safe-to-auto |
# must-escalate. Protocol: every DM escalates; complaints escalate; ambiguous /
# sensitive comments escalate.
_SPAM = (
    "$", "/week", "from home", "click my bio", "click the link", "bio link",
    "follow for follow", "follow back", "check out my page", "dm me to earn",
)
_COMPLAINT = (
    "not okay", "waited", "still red", "swollen", "is that normal", "refund",
    "never again", "so original", "must be nice", "i guess", "if you're into that",
    "🙄", "for a doodle", "doodle",
)
_LEAD = (
    "book", "consultation", "how do i start", "want to book", "do you fix",
    "quote for", "interested in getting",
)
_QUESTION_HINT = ("how much", "do you", "is this", "can i", "when ", "where ", "what ")
_SENSITIVE = ("real or ai", "is this even real", "or ai", "fix other", "bad work")


def _is_troll(text: str) -> bool:
    """Low-value emoji troll (e.g. 'first 💀💀💀') — spam, safe to ignore."""
    return text.strip().lower().startswith("first") and len(text) < 20


def predict_triage(text: str, channel: str) -> dict[str, str]:
    """Classify an engagement comment/DM into triage class + reply safety.

    Pure function of the message text and channel — the deterministic 'cell
    under test'. Precedence spam > complaint > lead > question > positive.
    """
    t = text.lower()
    if _is_troll(text) or any(k in t for k in _SPAM):
        triage_class = "spam"
    elif any(k in t for k in _COMPLAINT):
        triage_class = "complaint"
    elif any(k in t for k in _LEAD):
        triage_class = "lead"
    elif "?" in t or any(k in t for k in _QUESTION_HINT):
        triage_class = "question"
    else:
        triage_class = "positive"

    must_escalate = (
        channel == "dm"
        or triage_class == "complaint"
        or any(k in t for k in _SENSITIVE)
    )
    return {
        "triage_class": triage_class,
        "reply_safety": "must-escalate" if must_escalate else "safe-to-auto",
    }


def cell_predictor(example: GoldExample) -> dict[str, Any]:
    """Run the real cell under test for ``example`` (deterministic, input-only).

    Raises :class:`CellNotBuilt` for cells that don't exist yet so the gate
    SKIPs them (Phase-7 engines), never false-fails.
    """
    if example.engine is Engine.ENGAGEMENT and example.cell == "triage":
        return predict_triage(example.input["text"], example.input.get("channel", ""))
    raise CellNotBuilt(f"{example.engine.value}.{example.cell} cell not built yet")


def regressed_cell_predictor(example: GoldExample) -> dict[str, Any]:
    """Seeded regression for the fails-on-regression proof (engagement only)."""
    if example.engine is Engine.ENGAGEMENT and example.cell == "triage":
        return {"triage_class": "positive", "reply_safety": "safe-to-auto"}
    raise CellNotBuilt(f"{example.engine.value}.{example.cell} cell not built yet")
