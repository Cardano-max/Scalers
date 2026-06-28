"""SelectAngle (bead a9m.4) — pure-code, deterministic pick of one angle.

Per the Phase-3 ADR, angle *selection* stays in code (the model proposes the
candidates; code decides), so the chosen angle is reproducible at temp-0. This
module documents the selection criteria explicitly and de-dupes near-duplicate
candidates so two identical angles never flow downstream.

Selection criteria (in order, all deterministic):
  1. **De-dupe** candidates by a normalized hook (lowercased, punctuation/space
     collapsed); the first occurrence of each distinct hook is kept.
  2. **Score** each surviving angle by its grounding in the research:
     ``sum(item.score for item in research where the item shares a significant
     keyword with the angle's hook+rationale)``. More findings-aligned = higher.
  3. **Pick** the highest score; ties break by original candidate order (stable),
     so the same input always yields the same angle.
  4. **Low grounding** (empty/over-budget research): every score is 0, so the pick
     falls back to the first candidate and the selection is flagged
     ``low_grounding=True`` — a brand-only angle, never a fabricated one.

No viable candidate (empty set) raises :class:`NoViableAngleError`, which the
node maps to a route-to-review disposition (abort/regenerate), not a crash.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from cells.ideate import Angle, AngleSet
from research.content.items import ResearchResult

# Tokens too generic to count as "shared grounding".
_STOPWORDS = frozenset(
    {"the", "a", "an", "and", "or", "for", "to", "of", "in", "on", "with", "your",
     "you", "our", "this", "that", "is", "are", "it", "as", "at", "by", "be", "we"}
)
_MIN_TOKEN_LEN = 3


class NoViableAngleError(Exception):
    """No usable angle candidate — the slice routes to review (regenerate)."""


class AngleSelection(BaseModel):
    """The chosen angle + why it was chosen (the brief the draft cell commits to)."""

    model_config = {"frozen": True}

    angle: Angle
    reason: str = Field(description="Deterministic selection rationale.")
    score: float = Field(description="Grounding score of the chosen angle.")
    candidate_count: int = Field(description="Distinct candidates considered (post-dedupe).")
    low_grounding: bool = Field(description="True iff selection fell back to brand-only.")


def _norm_hook(hook: str) -> str:
    # Map any run of non-alphanumerics to a single space + collapse, so
    # "Cover-up regret!" and "cover up regret" normalize to the same key.
    return re.sub(r"[^a-z0-9]+", " ", hook.lower()).strip()


def _keywords(text: str) -> set[str]:
    toks = re.findall(r"[a-z0-9]+", text.lower())
    return {t for t in toks if len(t) >= _MIN_TOKEN_LEN and t not in _STOPWORDS}


def _dedupe(angles: list[Angle]) -> list[Angle]:
    seen: set[str] = set()
    out: list[Angle] = []
    for a in angles:
        key = _norm_hook(a.hook)
        if key and key not in seen:
            seen.add(key)
            out.append(a)
    return out


def _grounding_score(angle: Angle, research: ResearchResult) -> float:
    kw = _keywords(angle.hook + " " + angle.rationale)
    if not kw:
        return 0.0
    total = 0.0
    for item in research.items:
        if kw & _keywords(item.text):
            total += item.score
    return total


def select_angle(
    angle_set: AngleSet,
    research: ResearchResult | None,
    *,
    low_grounding: bool = False,
) -> AngleSelection:
    """Pick one angle from the candidates (criteria documented above). Raises
    :class:`NoViableAngleError` if there is no usable candidate."""
    candidates = _dedupe(list(angle_set.angles))
    if not candidates:
        raise NoViableAngleError("no viable angle candidates after de-dup")

    thin = low_grounding or research is None or not research.items
    if thin:
        # Brand-only fallback: deterministic first candidate, flagged.
        chosen = candidates[0]
        return AngleSelection(
            angle=chosen,
            reason="low grounding (thin/over-budget research) — brand-only angle, "
            "first distinct candidate chosen deterministically",
            score=0.0,
            candidate_count=len(candidates),
            low_grounding=True,
        )

    # Score + pick highest; stable tie-break on original order.
    scored = [(i, c, _grounding_score(c, research)) for i, c in enumerate(candidates)]
    best_idx, best_angle, best_score = max(scored, key=lambda t: (t[2], -t[0]))
    return AngleSelection(
        angle=best_angle,
        reason=f"highest research-grounding score ({best_score:.2f}) among "
        f"{len(candidates)} distinct candidates",
        score=best_score,
        candidate_count=len(candidates),
        low_grounding=False,
    )
