"""Per-lead marketing SKILL selection (P2-B, bead CustomerAcq-65w.6).

The supervisor should pick the RIGHT marketing play for each lead by its situation —
a warm price objection wants objection-recovery framing; a quiet past customer wants
re-engagement; a recurring regular wants a loyalty touch; a cold lead with no history
wants a warm intro. Until now that choice was implicit in ``customer_research._choose_angle``.
This module makes it a FIRST-CLASS, EVIDENCED decision recorded as ``skill_used`` on each
draft's ``agent_run``, mapping the per-lead :class:`~studio.dossier.Dossier` to one play.

## Supply-chain compliance — READ THIS (why this is first-party, not a pack load)

The upgrade design (Part C) names re-authored skillpacks under ``engine/studio/skillpacks/``
(``marketing_playbook``, ``research_ops``, ``growth_marketing_patterns``,
``customer_psychology``). Per the HARD RULE (``CLAUDE.md`` + ``docs/skills/registry.md``,
CI-enforced by ``scripts/check_skill_registry.py``): a skill may be loaded / referenced in a
prompt / executed ONLY with a ``REGISTERED-IN-USE`` registry row. As of this build the
registry has **zero** registered rows — every pack is ``IN-VETTING``. So this selector:

  * does **NOT** import any pack ``loader.py``, run any bundled script, or inject any pack
    ``SKILL.md`` prose into a model prompt;
  * uses only **our own first-party** guidance/tone prose (authored here);
  * records, per play, an honestly-labeled ``aligned_pack`` POINTER — the domain the play
    *would* draw on once that pack is registered — flagged ``pack_status`` so no one mistakes
    a routing label for a live dependency.

When a pack clears the gate, its vetted guidance can replace the first-party ``guidance`` here
through our own adapter — the routing contract (``select_skill`` → ``SkillSelection``) does not
change. Deterministic and keyless: works with no AI.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from studio.dossier import Dossier

# Honest label attached to every selection: the aligned packs are NOT loaded/executed.
_PACK_STATUS = "IN-VETTING — not loaded/executed; guidance is first-party (registry-gated)"


class SkillSelection(BaseModel):
    """The evidenced result of routing one lead to one marketing play.

    ``skill_id`` — our first-party play id (recorded as the draft's ``skill_used``).
    ``why`` — the traceable reason, grounded in the dossier's real signals.
    ``guidance`` / ``tone`` — first-party prose shaping the angle/voice (no pack loaded).
    ``aligned_pack`` — an honest pointer to the IN-VETTING pack whose domain this play maps
    to; ``pack_status`` makes clear it is not a live dependency.
    ``angle_key`` — links back to the ``customer_research`` angle the draft already leads with."""

    skill_id: str
    why: str
    guidance: str
    tone: str
    aligned_pack: str
    pack_status: str = _PACK_STATUS
    angle_key: str | None = None


class _Play(BaseModel):
    skill_id: str
    guidance: str
    tone: str
    aligned_pack: str


# Our first-party marketing plays. Each carries authored guidance/tone (NOT pack prose) and
# an honest pointer to the pack domain it aligns with. Keyed by a stable play id.
_PLAYS: dict[str, _Play] = {
    "objection-recovery": _Play(
        skill_id="objection-recovery",
        guidance=("Acknowledge the price/payment hesitation directly and warmly; offer a "
                  "genuine lower-commitment path (a real substantiated offer if one exists, "
                  "else a smaller/flash piece or an open reply). Never invent a discount."),
        tone="warm, reassuring, low-pressure",
        aligned_pack="marketing_playbook",
    ),
    "timing-nudge": _Play(
        skill_id="timing-nudge",
        guidance=("Keep the door open with zero pressure; make it easy to come back whenever "
                  "the timing is right. No urgency, no fake deadline."),
        tone="patient, no-pressure",
        aligned_pack="marketing_playbook",
    ),
    "trust-building": _Play(
        skill_id="trust-building",
        guidance=("Reassure with real proof — healed work, first-timer care, the process — "
                  "so a hesitant lead feels safe. No hype, no unverifiable claims."),
        tone="calm, credible, proof-led",
        aligned_pack="marketing_playbook",
    ),
    "decision-support": _Play(
        skill_id="decision-support",
        guidance=("Offer a relaxed, no-pressure consult to help an undecided lead figure out "
                  "what they want. Make deciding easier, never pushier."),
        tone="helpful, unhurried",
        aligned_pack="marketing_playbook",
    ),
    "re-engagement": _Play(
        skill_id="re-engagement",
        guidance=("Reconnect with a quiet/lapsed past customer as a genuine human follow-up — "
                  "you remember them, you'd love to see them back. Reference only real history."),
        tone="warm, personal, welcoming-back",
        aligned_pack="growth_marketing_patterns",
    ),
    "loyalty-touchup": _Play(
        skill_id="loyalty-touchup",
        guidance=("Treat a recurring regular like the loyal client they are — a touch-up / "
                  "next-piece invite that values the relationship. Reward loyalty honestly."),
        tone="familiar, appreciative",
        aligned_pack="growth_marketing_patterns",
    ),
    "completion-nudge": _Play(
        skill_id="completion-nudge",
        guidance=("Gently nudge a started-but-unfinished booking to completion; make finishing "
                  "effortless and pressure-free."),
        tone="gentle, encouraging",
        aligned_pack="growth_marketing_patterns",
    ),
    "shared-craft": _Play(
        skill_id="shared-craft",
        guidance=("Open on a real shared interest/aesthetic on file so the message could only "
                  "have been written to this lead. Stay strictly within their real interest."),
        tone="genuine, kindred, specific",
        aligned_pack="marketing_playbook",
    ),
    "warm-intro": _Play(
        skill_id="warm-intro",
        guidance=("For a cold lead with no history, write an honest, warm general introduction "
                  "that leans only on the true reason for reaching out. Honest-general beats "
                  "fake-personal — manufacture nothing."),
        tone="warm, honest, unpretentious",
        aligned_pack="marketing_playbook",
    ),
}

# Objection value (from the grounded psych read) -> play id.
_OBJECTION_TO_PLAY: dict[str, str] = {
    "price": "objection-recovery",
    "payment": "objection-recovery",
    "timing": "timing-nudge",
    "trust": "trust-building",
    "uncertainty": "decision-support",
}


def _norm(text: str | None) -> str:
    return (text or "").strip().lower()


def _segment_play(segment: str) -> str | None:
    """Map a customer_type / lifecycle / umbrella-category string to a lifecycle play.
    Tolerant of the free-text segments a real CSV uses ("recurring regular", "past
    customer", "cold lead") AND the psych analyst's umbrella categories."""
    s = _norm(segment)
    if not s:
        return None
    if any(k in s for k in ("recurring", "regular", "loyal", "repeat")):
        return "loyalty-touchup"
    if any(k in s for k in ("unpaid", "converted-but-unpaid", "deposit", "incomplete")):
        return "completion-nudge"
    if any(k in s for k in ("past", "lapsed", "lapsing", "win-back", "win_back", "reactivation",
                            "churn", "quiet", "dormant", "lead-no-visit")):
        return "re-engagement"
    if "cold" in s:
        return "warm-intro"
    return None


def select_skill(dossier: "Dossier") -> SkillSelection:
    """Route ONE lead's :class:`~studio.dossier.Dossier` to ONE first-party marketing play.

    Deterministic priority (most lead-specific first):
      1. a GROUNDED objection (stated/inferred) -> the matching recovery/timing/trust play;
      2. else the lead's SEGMENT (customer_type / lifecycle / psych category) -> the
         re-engagement / loyalty / completion / warm-intro play;
      3. else a real INTEREST on file -> shared-craft;
      4. else -> warm-intro (honest general introduction).

    The ``why`` traces to the dossier's real signals; ``angle_key`` links to the angle the
    draft already leads with. No fabrication, no skill load, no model call."""
    # 1) Grounded objection is the strongest, most lead-specific signal.
    obj = dossier.likely_objection
    if obj.present and _norm(str(obj.value)) in _OBJECTION_TO_PLAY:
        play = _PLAYS[_OBJECTION_TO_PLAY[_norm(str(obj.value))]]
        ev = (f': "{dossier.objection_evidence[:110]}"' if dossier.objection_evidence else "")
        why = (f"Lead voiced a {obj.value} objection ({obj.source}){ev}; "
               f"routing to {play.skill_id} framing.")
        return _selection(play, why, dossier)

    # 2) Lifecycle / segment.
    seg_source = dossier.customer_type.value or ""
    seg_play_id = _segment_play(str(seg_source))
    if seg_play_id is None:
        # Fall back to the grounded psych category if the segment column was blank.
        seg_play_id = _segment_play(dossier.best_angle.value or "")
    if seg_play_id is not None:
        play = _PLAYS[seg_play_id]
        why = (f"Segment '{seg_source or dossier.best_angle.value}' "
               f"({dossier.customer_type.source}) with no active objection; "
               f"routing to {play.skill_id}.")
        return _selection(play, why, dossier)

    # 3) A real interest on file -> lead on shared craft.
    if dossier.tattoo_interest.present:
        play = _PLAYS["shared-craft"]
        why = (f"No objection or lifecycle signal, but a real interest on file "
               f"({dossier.tattoo_interest.value}); routing to shared-craft.")
        return _selection(play, why, dossier)

    # 4) Honest default: warm general introduction.
    play = _PLAYS["warm-intro"]
    why = ("No grounded objection, segment, or interest on file; routing to an honest "
           "warm introduction (no manufactured personalization).")
    return _selection(play, why, dossier)


def _selection(play: _Play, why: str, dossier: "Dossier") -> SkillSelection:
    angle_key = None
    src = dossier.best_angle.source or ""
    if src.startswith("angle:"):
        angle_key = src.split(":", 1)[1].split("+", 1)[0]
    return SkillSelection(
        skill_id=play.skill_id, why=why, guidance=play.guidance, tone=play.tone,
        aligned_pack=play.aligned_pack, angle_key=angle_key,
    )
