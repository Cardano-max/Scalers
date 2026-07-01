"""psych_profile — the deep, evidence-grounded customer-PSYCHOLOGY analyst (P1 #1).

This is OUR OWN vetted analyst agent (a prompt + adapter we own, NOT a loaded
third-party skill — the registry gate forbids that): given ONE lead's real facts +
prior conversation + any social signal, it decides exactly WHERE that customer sits,
across many psychological dimensions, and grounds EVERY read in a real span of the
customer's own data.

The dimensions are grounded in public frameworks (cited so the read is principled, not
vibes):
  * ``readiness_stage`` — the buyer-readiness / Hierarchy-of-Effects ladder
    (awareness -> knowledge -> liking -> preference -> conviction -> purchase); see the
    Monash marketing dictionary + marketing91 buyer-readiness write-ups.
  * ``primary_objection`` / ``decision_blockers`` — the standard sales-objection
    taxonomy (price/budget, timing/urgency, trust/risk, need-value/uncertainty,
    payment) and the "diagnose the resistance before you respond" principle
    (prospeo.io, highspot, salesforce objection-handling guides).
  * the per-aspect, evidence-span reads follow aspect-based sentiment analysis (ABSA):
    a read is an (aspect, opinion-span, polarity) triple, not one blob sentiment
    (systematic review of ABSA, arXiv 2311.10777).

HARD ANTI-FABRICATION (the project's #1 gate, operator's headline demand):
  * Every dimension is a :class:`PsychField` tagged ``stated`` | ``inferred`` |
    ``insufficient-signal`` and carries the VERBATIM evidence span it is grounded on.
  * A deterministic, rule-based analyzer over :mod:`studio.reason_history` signals + the
    lead's real CRM facts is the FLOOR (and the keyless fallback). An optional LLM pass
    can ENRICH it, but its output is run through the SAME corpus-validation gate:
    :func:`_validate_against_corpus` DOWNGRADES to ``insufficient-signal`` any ``stated``
    read whose evidence does not literally appear in the lead's own text, and any
    ``inferred`` read whose claimed source has no data. The model can propose; only
    real evidence survives.
  * No signal for a dimension -> ``insufficient-signal`` (value dropped). We NEVER invent
    a psychology, a motive, or an objection that the customer's data does not evidence.

The analyst runs per-lead BEFORE strategy/draft in the provided-leads path and is
recorded as a real ``agent_run(role="analyst")`` so it shows in the lanes honestly.
"""

from __future__ import annotations

import os
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from studio.reason_history import (
    OBJECTION_PAYMENT,
    OBJECTION_PRICE,
    OBJECTION_TIMING,
    OBJECTION_TRUST,
    OBJECTION_UNCERTAINTY,
    ReasonSignals,
    extract_signals,
)

# --------------------------------------------------------------------------- #
# Signal tags + controlled vocabularies (kept explicit so an LLM value that is
# off-enum is clamped, never passed through as a novel fabricated category).
# --------------------------------------------------------------------------- #
STATED = "stated"
INFERRED = "inferred"
INSUFFICIENT = "insufficient-signal"

# The five umbrella categories (the operator's canonical set).
CAT_OPEN = "open-warm-lead"
CAT_ARTIST = "artist-specific-warm-lead"
CAT_UNPAID = "converted-but-unpaid"
CAT_RECURRING = "recurring-customer"
CAT_REACTIVATION = "past-customer-reactivation"
_CATEGORIES = frozenset(
    {CAT_OPEN, CAT_ARTIST, CAT_UNPAID, CAT_RECURRING, CAT_REACTIVATION}
)

_OBJECTIONS = frozenset(
    {OBJECTION_PRICE, OBJECTION_TIMING, OBJECTION_TRUST, OBJECTION_UNCERTAINTY,
     OBJECTION_PAYMENT, "none-found"}
)
_LEVELS = frozenset({"low", "moderate", "high"})
# Buyer-readiness / Hierarchy-of-Effects stages.
_STAGES = frozenset(
    {"awareness", "knowledge", "liking", "preference", "conviction", "purchase"}
)

# Evidence-source tags — which real surface a read came from.
SRC_CONVERSATION = "conversation"
SRC_CSV = "csv"
SRC_PERSONA = "persona"
SRC_HISTORY = "tattoo_history"
SRC_SOCIAL = "social"


class _Camel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class PsychField(_Camel):
    """One psychological dimension: its value, how strongly it is grounded (``signal``),
    the VERBATIM evidence span it rests on, and which real surface that came from.

    A ``stated`` read = the customer's own words evidence it. An ``inferred`` read =
    derived from a real CRM/persona field (marked, never asserted as fact). An
    ``insufficient-signal`` read = no real evidence, so the value is dropped (empty)."""

    value: str = ""
    signal: str = INSUFFICIENT
    evidence: str = ""
    evidence_source: str = ""

    @classmethod
    def insufficient(cls, note: str = "") -> "PsychField":
        return cls(value="", signal=INSUFFICIENT, evidence=note, evidence_source="")


class PsychProfile(_Camel):
    """The full, evidence-grounded psychological read of one customer — WHERE they sit."""

    customer_id: str | None = None
    umbrella_category: PsychField = Field(default_factory=PsychField)
    primary_objection: PsychField = Field(default_factory=PsychField)
    secondary_objections: list[PsychField] = Field(default_factory=list)
    intent_strength: PsychField = Field(default_factory=PsychField)
    urgency: PsychField = Field(default_factory=PsychField)
    price_sensitivity: PsychField = Field(default_factory=PsychField)
    trust_level: PsychField = Field(default_factory=PsychField)
    readiness_stage: PsychField = Field(default_factory=PsychField)
    emotional_tone: PsychField = Field(default_factory=PsychField)
    decision_blockers: list[PsychField] = Field(default_factory=list)
    # Derived one-liners, composed from the SURVIVING (post-validation) fields only.
    best_reengagement_angle: str = ""
    where_customer_sits: str = ""
    # Provenance / honesty meta.
    source: str = "deterministic"  # deterministic | deterministic+llm
    grounded_fields: int = 0
    insufficient_fields: int = 0
    had_conversation: bool = False

    def scalar_fields(self) -> list[tuple[str, PsychField]]:
        """The single-value dimensions (excludes the list dimensions)."""
        return [
            ("umbrella_category", self.umbrella_category),
            ("primary_objection", self.primary_objection),
            ("intent_strength", self.intent_strength),
            ("urgency", self.urgency),
            ("price_sensitivity", self.price_sensitivity),
            ("trust_level", self.trust_level),
            ("readiness_stage", self.readiness_stage),
            ("emotional_tone", self.emotional_tone),
        ]


# --------------------------------------------------------------------------- #
# Corpus + present-source computation (the anti-fabrication substrate).
# --------------------------------------------------------------------------- #
_NORM_RE = re.compile(r"[^a-z0-9]+")


def _norm(text: str) -> str:
    """Normalize for the evidence-match: lowercase, collapse all non-alphanumeric runs
    (punctuation + whitespace) to single spaces, strip. So a ``stated`` read's verbatim
    quote survives trivial differences ("short on budget" vs "a bit short on budget,")
    but a genuinely absent quote still fails the substring check. We normalize BOTH the
    corpus and the evidence identically, keeping substring semantics — always erring
    toward downgrade (insufficient-signal) over a fabricated read when uncertain."""
    return _NORM_RE.sub(" ", (text or "").lower()).strip()
def _facts_kv(facts: dict[str, Any]) -> list[str]:
    """The lead's real CRM facts as ``key=value`` tokens (so an ``inferred`` read that
    references a real field, and a ``stated`` read on a CSV value, can be corpus-checked).
    Only real, present values are included — an absent field contributes nothing."""
    kv: list[str] = []
    traits = facts.get("persona_traits", {}) or {}
    for k in ("name", "city", "state", "notes", "customer_type", "lead_stage",
              "payment_status", "artist", "shop"):
        v = facts.get(k)
        if v:
            kv.append(f"{k}={v}")
    for i in facts.get("interests", []) or []:
        if i:
            kv.append(f"interest={i}")
    for tk, tv in traits.items():
        if tv is not None and str(tv).strip():
            kv.append(f"{tk}={tv}")
    for t in facts.get("tattoo_history", []) or []:
        st = (t or {}).get("style")
        if st:
            kv.append(f"past_style={st}")
    return kv


def _build_corpus(
    facts: dict[str, Any], signals: ReasonSignals, social: str | None,
    conversation_turns: list[dict[str, Any]] | None,
) -> str:
    """The lowercased text a ``stated`` read must be traceable to: the real conversation
    turns + the CRM ``key=value`` tokens + any social text. This is the ground truth the
    validation gate checks every evidence span against."""
    parts: list[str] = []
    for t in conversation_turns or []:
        parts.append(str(t.get("text") or ""))
    parts.extend(_facts_kv(facts))
    if social:
        parts.append(str(social))
    # Normalized (case/whitespace/punctuation-collapsed) so a verbatim quote matches
    # despite trivial differences; the evidence is normalized identically at compare time.
    return _norm("\n".join(parts))


def _present_sources(
    facts: dict[str, Any], signals: ReasonSignals, social: str | None
) -> set[str]:
    """Which real surfaces actually carry data for this lead — an ``inferred`` read may
    only claim a source that is genuinely present (so an LLM cannot infer 'from the
    conversation' when there is no conversation)."""
    present: set[str] = set()
    if signals.has_conversation:
        present.add(SRC_CONVERSATION)
    traits = facts.get("persona_traits", {}) or {}
    if traits:
        present.add(SRC_PERSONA)
    if any(facts.get(k) for k in ("name", "city", "notes", "interests",
                                  "customer_type", "lead_stage", "payment_status",
                                  "artist", "shop")):
        present.add(SRC_CSV)
    if facts.get("tattoo_history"):
        present.add(SRC_HISTORY)
    if social:
        present.add(SRC_SOCIAL)
    return present


def _validate_field(field: PsychField, corpus: str, present: set[str]) -> PsychField:
    """The anti-fabrication gate for one field. A ``stated`` read survives ONLY if its
    evidence literally appears in the lead's own text; an ``inferred`` read survives ONLY
    if its claimed source genuinely has data. Anything else is downgraded to
    ``insufficient-signal`` with its value dropped — never a fabricated psychology."""
    if field.signal == INSUFFICIENT:
        return PsychField.insufficient(field.evidence)
    if not field.value.strip():
        return PsychField.insufficient(field.evidence)
    if field.signal == STATED:
        ev = _norm(field.evidence)
        if ev and ev in corpus:
            return field
        return PsychField.insufficient("stated read failed corpus grounding; dropped")
    if field.signal == INFERRED:
        if field.evidence_source in present and field.evidence.strip():
            return field
        return PsychField.insufficient("inferred read had no present source; dropped")
    # Unknown signal tag -> treat as ungrounded.
    return PsychField.insufficient("unknown signal tag; dropped")


# --------------------------------------------------------------------------- #
# Deterministic analyzer — the grounded FLOOR + keyless fallback. Every read below
# is built from a real signal span or a real CRM field; nothing is invented.
# --------------------------------------------------------------------------- #
def _lifecycle(facts: dict[str, Any]) -> str | None:
    return (facts.get("persona_traits", {}) or {}).get("lifecycle_stage")


def _detect_category(facts: dict[str, Any], signals: ReasonSignals) -> PsychField:
    traits = facts.get("persona_traits", {}) or {}
    ctype = str(facts.get("customer_type") or "").strip().lower()
    payment = str(facts.get("payment_status") or "").strip().lower()
    lifecycle = str(_lifecycle(facts) or "").strip().lower()
    artist = facts.get("artist") or (signals.artists[0].value if signals.artists else None)
    win_back = bool(traits.get("win_back_candidate"))
    n_history = len(facts.get("tattoo_history", []) or [])

    # An explicit customer_type field is the strongest signal (stated CRM classification).
    explicit = {
        "converted_unpaid": (CAT_UNPAID, f"customer_type={ctype}"),
        "converted-but-unpaid": (CAT_UNPAID, f"customer_type={ctype}"),
        "recurring": (CAT_RECURRING, f"customer_type={ctype}"),
        "recurring_customer": (CAT_RECURRING, f"customer_type={ctype}"),
        "past": (CAT_REACTIVATION, f"customer_type={ctype}"),
        "reactivation": (CAT_REACTIVATION, f"customer_type={ctype}"),
        "artist_specific": (CAT_ARTIST, f"customer_type={ctype}"),
        "open": (CAT_OPEN, f"customer_type={ctype}"),
    }
    if ctype in explicit:
        cat, ev = explicit[ctype]
        return PsychField(value=cat, signal=INFERRED, evidence=ev, evidence_source=SRC_CSV)

    if payment in ("unpaid", "deposit", "partial", "owed"):
        return PsychField(value=CAT_UNPAID, signal=INFERRED,
                          evidence=f"payment_status={payment}", evidence_source=SRC_CSV)
    if artist:
        return PsychField(value=CAT_ARTIST, signal=INFERRED,
                          evidence=f"artist={artist}", evidence_source=SRC_CSV)
    if lifecycle in ("lapsing", "lead-no-visit", "churn-risk", "past", "lapsed") or win_back:
        return PsychField(value=CAT_REACTIVATION, signal=INFERRED,
                          evidence=f"lifecycle_stage={lifecycle or 'win_back_candidate'}",
                          evidence_source=SRC_PERSONA)
    if lifecycle in ("recurring", "loyal", "repeat") or n_history >= 2:
        ev = f"lifecycle_stage={lifecycle}" if lifecycle else f"past_style={(facts.get('tattoo_history') or [{}])[0].get('style')}"
        return PsychField(value=CAT_RECURRING, signal=INFERRED,
                          evidence=ev, evidence_source=SRC_PERSONA if lifecycle else SRC_HISTORY)
    return PsychField(value=CAT_OPEN, signal=INFERRED,
                      evidence="no artist/lifecycle/payment signal -> open lead",
                      evidence_source=SRC_CSV if _present_has_csv(facts) else "")


def _present_has_csv(facts: dict[str, Any]) -> bool:
    return any(facts.get(k) for k in ("name", "city", "notes", "interests"))


def _objection_fields(signals: ReasonSignals) -> tuple[PsychField, list[PsychField]]:
    if not signals.has_conversation:
        return PsychField.insufficient("no conversation to read an objection from"), []
    objs = signals.objections
    if not objs:
        last = signals.last_customer_message or ""
        return (
            PsychField(value="none-found", signal=INFERRED,
                       evidence=(last or "conversation read; no objection phrase present"),
                       evidence_source=SRC_CONVERSATION),
            [],
        )
    primary = PsychField(value=objs[0].value, signal=STATED,
                         evidence=objs[0].evidence, evidence_source=SRC_CONVERSATION)
    secondary = [
        PsychField(value=o.value, signal=STATED, evidence=o.evidence,
                   evidence_source=SRC_CONVERSATION)
        for o in objs[1:]
    ]
    return primary, secondary


def _price_sensitivity(facts: dict[str, Any], signals: ReasonSignals) -> PsychField:
    price = next((o for o in signals.objections if o.value == OBJECTION_PRICE), None)
    if price is not None:
        return PsychField(value="high", signal=STATED, evidence=price.evidence,
                          evidence_source=SRC_CONVERSATION)
    trait = (facts.get("persona_traits", {}) or {}).get("price_sensitivity")
    if trait and str(trait).lower() in _LEVELS:
        return PsychField(value=str(trait).lower(), signal=INFERRED,
                          evidence=f"price_sensitivity={trait}", evidence_source=SRC_PERSONA)
    return PsychField.insufficient("no price signal in conversation or persona")


def _urgency(signals: ReasonSignals) -> PsychField:
    if signals.urgency is not None:
        return PsychField(value=signals.urgency.value, signal=STATED,
                          evidence=signals.urgency.evidence, evidence_source=SRC_CONVERSATION)
    return PsychField.insufficient("no urgency cue stated")


def _intent_strength(facts: dict[str, Any], signals: ReasonSignals) -> PsychField:
    if signals.urgency and signals.urgency.value == "high":
        return PsychField(value="high", signal=STATED, evidence=signals.urgency.evidence,
                          evidence_source=SRC_CONVERSATION)
    if signals.styles:
        # Named a concrete style/subject they want = a real, moderate buying interest.
        s = signals.styles[0]
        val = "low" if (signals.urgency and signals.urgency.value == "low") else "moderate"
        return PsychField(value=val, signal=STATED, evidence=s.evidence,
                          evidence_source=SRC_CONVERSATION)
    lifecycle = str(_lifecycle(facts) or "").lower()
    if lifecycle in ("lapsing", "lead-no-visit", "churn-risk", "past", "lapsed"):
        return PsychField(value="low", signal=INFERRED,
                          evidence=f"lifecycle_stage={lifecycle}", evidence_source=SRC_PERSONA)
    return PsychField.insufficient("no intent cue in conversation or lifecycle")


def _trust_level(facts: dict[str, Any], signals: ReasonSignals) -> PsychField:
    trust_obj = next((o for o in signals.objections if o.value == OBJECTION_TRUST), None)
    if trust_obj is not None:
        return PsychField(value="low", signal=STATED, evidence=trust_obj.evidence,
                          evidence_source=SRC_CONVERSATION)
    if (facts.get("tattoo_history") or []):
        style = (facts["tattoo_history"][0] or {}).get("style")
        return PsychField(value="high", signal=INFERRED,
                          evidence=f"past_style={style}" if style else "past piece on file",
                          evidence_source=SRC_HISTORY)
    return PsychField.insufficient("no trust signal (no trust objection, no prior work)")


def _readiness_stage(
    category: PsychField, primary_obj: PsychField, signals: ReasonSignals,
) -> PsychField:
    """Map to the buyer-readiness ladder from real signals. A lead who named a style and
    voiced a preference-then-blocker sits at ``preference``; a converted-unpaid lead has
    already reached ``conviction``; a recurring customer has hit ``purchase``; a lapsed
    lead re-enters at ``liking``. Grounded on the evidence that drove the category/objection."""
    cat = category.value
    if cat == CAT_UNPAID:
        return PsychField(value="conviction", signal=INFERRED, evidence=category.evidence,
                          evidence_source=category.evidence_source or SRC_CSV)
    if cat == CAT_RECURRING:
        return PsychField(value="purchase", signal=INFERRED, evidence=category.evidence,
                          evidence_source=category.evidence_source or SRC_HISTORY)
    if signals.styles and signals.has_conversation:
        # Named a concrete piece -> at least knowledge; a like/objection -> preference.
        if primary_obj.signal == STATED or "like" in (signals.last_customer_message or "").lower():
            return PsychField(value="preference", signal=STATED,
                              evidence=signals.styles[0].evidence, evidence_source=SRC_CONVERSATION)
        return PsychField(value="knowledge", signal=STATED,
                          evidence=signals.styles[0].evidence, evidence_source=SRC_CONVERSATION)
    if cat == CAT_REACTIVATION:
        return PsychField(value="liking", signal=INFERRED, evidence=category.evidence,
                          evidence_source=category.evidence_source or SRC_PERSONA)
    return PsychField.insufficient("not enough signal to place a readiness stage")


_POSITIVE_TONE = ("i like", "love", "excited", "beautiful", "obsessed", "can't wait",
                  "cant wait", "amazing", "please", "yes please")
_HESITANT_TONE = ("nervous", "not sure", "maybe", "worried", "hesitant", "scared")


def _emotional_tone(signals: ReasonSignals) -> PsychField:
    if not signals.has_conversation:
        return PsychField.insufficient("no conversation to read tone from")
    # Read the strongest tone cue from the customer's own words (ABSA opinion span).
    joined_last = signals.last_customer_message or ""
    # Scan all customer evidence spans we captured for a positive/hesitant marker.
    spans = [o.evidence for o in signals.objections] + [s.evidence for s in signals.styles]
    spans.append(joined_last)
    for span in spans:
        low = (span or "").lower()
        for p in _POSITIVE_TONE:
            if p in low:
                return PsychField(value="interested-but-blocked" if signals.objections else "warm-interested",
                                  signal=INFERRED, evidence=span, evidence_source=SRC_CONVERSATION)
    for span in spans:
        low = (span or "").lower()
        for h in _HESITANT_TONE:
            if h in low:
                return PsychField(value="hesitant", signal=INFERRED, evidence=span,
                                  evidence_source=SRC_CONVERSATION)
    return PsychField(value="neutral", signal=INFERRED,
                      evidence=joined_last or "conversation present, tone neutral",
                      evidence_source=SRC_CONVERSATION)


def _deterministic_profile(
    facts: dict[str, Any], signals: ReasonSignals, social: str | None,
) -> PsychProfile:
    category = _detect_category(facts, signals)
    primary_obj, secondary = _objection_fields(signals)
    profile = PsychProfile(
        customer_id=facts.get("customer_id"),
        umbrella_category=category,
        primary_objection=primary_obj,
        secondary_objections=secondary,
        intent_strength=_intent_strength(facts, signals),
        urgency=_urgency(signals),
        price_sensitivity=_price_sensitivity(facts, signals),
        trust_level=_trust_level(facts, signals),
        readiness_stage=_readiness_stage(category, primary_obj, signals),
        emotional_tone=_emotional_tone(signals),
        decision_blockers=[
            PsychField(value=o.value, signal=STATED, evidence=o.evidence,
                       evidence_source=SRC_CONVERSATION)
            for o in signals.objections
        ],
        had_conversation=signals.has_conversation,
    )
    return profile


# --------------------------------------------------------------------------- #
# Derived narrative — composed ONLY from the surviving (validated) fields, so the
# one-liners can never mention a psychology that failed grounding.
# --------------------------------------------------------------------------- #
def _first_style(signals: ReasonSignals) -> str | None:
    return signals.styles[0].value if signals.styles else None


_ANGLE_BY_OBJECTION = {
    OBJECTION_PRICE: "offer a real returning-inquiry discount or a small-piece/flash option",
    OBJECTION_TIMING: "a low-pressure, flexible-slot nudge for whenever they're ready",
    OBJECTION_TRUST: "share healed portfolio work + first-timer / hygiene reassurance",
    OBJECTION_PAYMENT: "offer a deposit or payment-split path",
    OBJECTION_UNCERTAINTY: "a no-pressure consult to help them decide",
}


def _compose_narrative(profile: PsychProfile, signals: ReasonSignals) -> None:
    """Fill ``best_reengagement_angle`` + ``where_customer_sits`` from surviving fields."""
    style = _first_style(signals)
    style_bit = f" {style}" if style else ""
    cat = profile.umbrella_category.value
    obj = profile.primary_objection.value

    # Re-engagement angle: objection-first, else category-first.
    if obj in _ANGLE_BY_OBJECTION:
        angle = _ANGLE_BY_OBJECTION[obj]
        if obj in (OBJECTION_PRICE, OBJECTION_UNCERTAINTY, OBJECTION_TRUST) and style:
            angle += f" on the{style_bit} they wanted"
    elif cat == CAT_RECURRING:
        angle = "invite them back for a touch-up or their next piece (loyalty)"
    elif cat == CAT_UNPAID:
        angle = "a gentle nudge to complete the booking they already committed to"
    elif cat == CAT_REACTIVATION:
        angle = "a warm we-miss-you with what's new since they last visited"
    elif style:
        angle = f"a warm intro leading with the{style_bit} style they liked"
    else:
        angle = "a warm, honest introduction to the studio"
    profile.best_reengagement_angle = angle

    # Where this customer sits: a one-line definition from real, surviving reads.
    bits: list[str] = []
    if cat:
        bits.append(cat.replace("-", " "))
    if style:
        bits.append(f"interested in{style_bit}")
    if profile.readiness_stage.value:
        bits.append(f"{profile.readiness_stage.value}-stage")
    if obj and obj != "none-found":
        bits.append(f"blocked on {obj}")
    elif obj == "none-found":
        bits.append("no objection surfaced")
    profile.where_customer_sits = (
        "; ".join(bits) if bits else "an early warm lead with limited signal on file"
    )


def _finalize(profile: PsychProfile, corpus: str, present: set[str],
              signals: ReasonSignals) -> PsychProfile:
    """Run the anti-fabrication gate over every field, recount grounding, and compose
    the derived one-liners from what survived."""
    profile.umbrella_category = _validate_field(profile.umbrella_category, corpus, present)
    profile.primary_objection = _validate_field(profile.primary_objection, corpus, present)
    profile.secondary_objections = [
        v for v in (_validate_field(o, corpus, present) for o in profile.secondary_objections)
        if v.signal != INSUFFICIENT
    ]
    profile.intent_strength = _validate_field(profile.intent_strength, corpus, present)
    profile.urgency = _validate_field(profile.urgency, corpus, present)
    profile.price_sensitivity = _validate_field(profile.price_sensitivity, corpus, present)
    profile.trust_level = _validate_field(profile.trust_level, corpus, present)
    profile.readiness_stage = _validate_field(profile.readiness_stage, corpus, present)
    profile.emotional_tone = _validate_field(profile.emotional_tone, corpus, present)
    profile.decision_blockers = [
        v for v in (_validate_field(b, corpus, present) for b in profile.decision_blockers)
        if v.signal != INSUFFICIENT
    ]

    scalars = [f for _, f in profile.scalar_fields()]
    profile.grounded_fields = sum(1 for f in scalars if f.signal != INSUFFICIENT)
    profile.insufficient_fields = sum(1 for f in scalars if f.signal == INSUFFICIENT)
    _compose_narrative(profile, signals)
    return profile


# --------------------------------------------------------------------------- #
# Optional LLM enrichment — proposes; the SAME gate validates. Off by default in
# tests / keyless envs (deterministic floor stands).
# --------------------------------------------------------------------------- #
def _llm_enabled() -> bool:
    override = os.environ.get("SCALERS_PSYCH_LLM")
    if override is not None:
        return override.strip().lower() in ("1", "true", "yes", "on")
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _clamp(field: PsychField, allowed: frozenset[str]) -> PsychField:
    """Reject an off-enum LLM value (never pass a novel fabricated category through)."""
    if field.value and field.value.strip().lower() not in allowed:
        return PsychField.insufficient(f"off-vocabulary value '{field.value}' rejected")
    if field.value:
        field.value = field.value.strip().lower()
    return field


def _merge_llm(base: PsychProfile, llm: "PsychLLMOut") -> PsychProfile:
    """Overlay an LLM read onto the deterministic floor field-by-field, but ONLY where the
    LLM produced a grounded, in-vocabulary read; otherwise the deterministic field stands.
    (Validation of evidence-vs-corpus happens afterwards in :func:`_finalize`.)"""
    def pick(base_field: PsychField, llm_field: PsychField | None, allowed: frozenset[str]) -> PsychField:
        if llm_field is None:
            return base_field
        clamped = _clamp(llm_field, allowed)
        if clamped.signal == INSUFFICIENT or not clamped.value:
            return base_field
        return clamped

    base.umbrella_category = pick(base.umbrella_category, llm.umbrella_category, _CATEGORIES)
    base.primary_objection = pick(base.primary_objection, llm.primary_objection, _OBJECTIONS)
    base.intent_strength = pick(base.intent_strength, llm.intent_strength, _LEVELS)
    base.urgency = pick(base.urgency, llm.urgency, _LEVELS)
    base.price_sensitivity = pick(base.price_sensitivity, llm.price_sensitivity, _LEVELS)
    base.trust_level = pick(base.trust_level, llm.trust_level, _LEVELS)
    base.readiness_stage = pick(base.readiness_stage, llm.readiness_stage, _STAGES)
    if llm.emotional_tone and llm.emotional_tone.value:
        base.emotional_tone = llm.emotional_tone
    # Secondary objections / blockers: keep the union but only in-vocabulary ones.
    for o in llm.secondary_objections or []:
        c = _clamp(o, _OBJECTIONS)
        if c.signal != INSUFFICIENT and c.value and c.value not in [
            x.value for x in base.secondary_objections
        ] + [base.primary_objection.value]:
            base.secondary_objections.append(c)
    base.source = "deterministic+llm"
    return base


class PsychLLMOut(_Camel):
    """The structured object the analyst LLM cell fills. Each dimension mirrors a
    :class:`PsychField` so the same validation gate applies to model output."""

    umbrella_category: PsychField | None = None
    primary_objection: PsychField | None = None
    secondary_objections: list[PsychField] = Field(default_factory=list)
    intent_strength: PsychField | None = None
    urgency: PsychField | None = None
    price_sensitivity: PsychField | None = None
    trust_level: PsychField | None = None
    readiness_stage: PsychField | None = None
    emotional_tone: PsychField | None = None


def psych_llm_model() -> str:
    """The REAL model id the LLM-enriched analyst runs at, read from the actual cell —
    so a caller records a TRUTHFUL ``agent_run.model`` for an LLM read instead of a
    hardcoded literal that could drift from the cell's pin."""
    m = getattr(_build_psych_cell(), "model", None)
    return m if isinstance(m, str) else str(m)


def _build_psych_cell():
    from cells.base import Cell
    from cells.validators import ValidatorBank

    instructions = (
        "You are a rigorous customer-PSYCHOLOGY analyst for a tattoo studio. Given ONE "
        "lead's real CRM facts and their prior conversation, decide WHERE this customer "
        "sits across the given psychological dimensions. Use the buyer-readiness ladder "
        "(awareness/knowledge/liking/preference/conviction/purchase) for readiness_stage "
        "and the objection taxonomy (price/timing/trust/uncertainty/payment/none-found) "
        "for objections.\n\n"
        "HARD RULES (a fabrication is a critical failure):\n"
        "- For EVERY dimension set signal to 'stated' ONLY if the customer's own words "
        "evidence it, and put that VERBATIM quote in 'evidence' with evidence_source="
        "'conversation'. Set 'inferred' if you derive it from a CRM/persona field (put "
        "the field in 'evidence', source 'csv'/'persona'/'tattoo_history'). Set "
        "'insufficient-signal' and leave value empty if there is NO real evidence.\n"
        "- NEVER invent an objection, motive, urgency, or emotion the data does not show. "
        "When unsure, choose 'insufficient-signal'. Honest gaps beat confident guesses."
    )
    return Cell(
        name="psych_analyst",
        schema=PsychLLMOut,
        instructions=instructions,
        validators=ValidatorBank(validators=()),
    )


def _build_psych_prompt(
    facts: dict[str, Any], conversation_turns: list[dict[str, Any]] | None,
    social: str | None,
) -> str:
    lines = ["# LEAD CRM FACTS (real, may be sparse):"]
    lines.extend(f"- {kv}" for kv in _facts_kv(facts))
    if not any(_facts_kv(facts)):
        lines.append("- (no CRM facts on file)")
    lines.append("\n# PRIOR CONVERSATION (the customer's own words — the primary evidence):")
    if conversation_turns:
        for t in conversation_turns:
            who = "CUSTOMER" if t.get("speaker") == "customer" else "STUDIO"
            lines.append(f"{who}: {t.get('text')}")
    else:
        lines.append("(no prior conversation on file — do NOT invent objections/psychology; "
                     "mark conversation-derived dimensions insufficient-signal)")
    if social:
        lines.append(f"\n# SOCIAL SIGNAL (verbatim): {social}")
    lines.append("\nProduce the structured psychological read. Ground every 'stated' read "
                 "in a verbatim quote above; use 'insufficient-signal' wherever there is no "
                 "real evidence.")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Public entry point.
# --------------------------------------------------------------------------- #
def analyze_customer(
    facts: dict[str, Any],
    conversation: list[dict[str, Any]] | dict[str, Any] | None = None,
    social: str | None = None,
    *,
    known_artists: list[str] | None = None,
    use_llm: bool | None = None,
) -> PsychProfile:
    """Analyze ONE customer into a deep, evidence-grounded :class:`PsychProfile`.

    ``facts`` is the lead's real grounded facts (as ``customer_research.lookup_lead``
    returns). ``conversation`` is either a list of ``{speaker,text}`` turns or the
    ``conversations.get_conversation`` dict (``{turns,...}``). ``social`` is any verbatim
    social snippet.

    A deterministic, fully-grounded read is always computed (the keyless floor). When an
    LLM is available (and not disabled) it enriches the read, but its output is passed
    through the SAME corpus-validation gate — no read survives that the customer's own
    data does not evidence. Returns the profile; every field is tagged stated/inferred/
    insufficient-signal and carries its evidence span. NEVER fabricates."""
    # Normalize the conversation input to a list of turns. Accepts a list of
    # ``{speaker,text}`` turns, the ``get_conversation`` dict (``{turns,...}``), OR a
    # message-source ``ConversationThread`` (has a ``.turns`` attribute) — the adapter's
    # own contract, so the analyst and the message-source adapters compose without the
    # caller having to unwrap. ``None`` / empty stays honestly empty.
    if isinstance(conversation, dict):
        turns = conversation.get("turns") or []
    elif conversation is not None and not isinstance(conversation, (list, tuple)) and hasattr(conversation, "turns"):
        turns = getattr(conversation, "turns") or []
    else:
        turns = conversation or []

    signals = extract_signals(turns, known_artists=known_artists)
    corpus = _build_corpus(facts, signals, social, turns)
    present = _present_sources(facts, signals, social)

    profile = _deterministic_profile(facts, signals, social)

    do_llm = _llm_enabled() if use_llm is None else use_llm
    if do_llm:
        try:
            cell = _build_psych_cell()
            out = cell.run_sync(_build_psych_prompt(facts, turns, social))
            profile = _merge_llm(profile, out)
        except Exception:
            # Any cell/model failure: the deterministic floor stands (honest, grounded).
            profile.source = "deterministic"

    return _finalize(profile, corpus, present, signals)
