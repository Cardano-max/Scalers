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

from research.protected_traits import (
    allowed_categories,
    build_first_party_corpus,
    filter_lines,
    trait_violations,
)
from studio.reason_history import (
    OBJECTION_BLOCKED_PREREQ,
    OBJECTION_PAYMENT,
    OBJECTION_PRICE,
    OBJECTION_TIMING,
    OBJECTION_TRUST,
    OBJECTION_TRUST_CONCERN,
    OBJECTION_UNCERTAINTY,
    OBJECTION_WENT_QUIET,
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
     OBJECTION_PAYMENT, OBJECTION_TRUST_CONCERN, OBJECTION_BLOCKED_PREREQ,
     OBJECTION_WENT_QUIET, "none-found"}
)

# Objection FAMILIES — derived from the existing taxonomy groups: reason_history's
# canonical sales-objection taxonomy pairs price/budget with payment (money), and
# trust/risk with the trust_concern breach (customer_research routes both to the
# same trust handling; its offer logic treats price+payment as one money family).
# Used by the S2 misclassification guard in :func:`_merge_llm`: when the
# deterministic floor found a STATED objection, the LLM overlay may refine WITHIN
# the same family (payment -> price, trust -> trust_concern, …) or add secondaries,
# but may NOT flip the primary to a DIFFERENT family unless it cites
# customer-quoted (not studio-quoted) evidence for the new family.
_OBJECTION_FAMILY: dict[str, str] = {
    OBJECTION_PRICE: "money", OBJECTION_PAYMENT: "money",
    OBJECTION_TRUST: "trust", OBJECTION_TRUST_CONCERN: "trust",
    OBJECTION_TIMING: "timing",
    OBJECTION_UNCERTAINTY: "uncertainty",
    OBJECTION_BLOCKED_PREREQ: "prerequisite",
    OBJECTION_WENT_QUIET: "went-quiet",
}


def objection_family(value: str | None) -> str:
    """The taxonomy family one objection value belongs to (an unknown value is its
    own family, so it can never silently equal another's)."""
    v = (value or "").strip().lower()
    return _OBJECTION_FAMILY.get(v, v)
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
SRC_MEMORY = "memory"


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
    # Sensitive-trait audit (spec §7/§24): every field/evidence line the deterministic
    # protected-traits filter DROPPED, recorded honestly — {field, categories, matched,
    # note} — so an operator can see exactly what was withheld and why. Empty = nothing
    # asserted a protected trait.
    trait_filtered: list[dict[str, str]] = Field(default_factory=list)

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
    turns + the CRM ``key=value`` tokens + the lead's prior-campaign MEMORIES + any
    social text. This is the ground truth the validation gate checks every evidence
    span against."""
    parts: list[str] = []
    for t in conversation_turns or []:
        parts.append(str(t.get("text") or ""))
    parts.extend(_facts_kv(facts))
    # Prior campaign memories (real ``memories`` rows for this customer, as
    # ``lookup_lead`` returns them) are first-party CRM evidence: a stated read may
    # quote them, and they ground the "what we already tried / learned" dimension.
    for m in facts.get("memories") or []:
        txt = (m or {}).get("text") if isinstance(m, dict) else None
        if txt:
            parts.append(str(txt))
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
    if facts.get("memories"):
        present.add(SRC_MEMORY)
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


def _objection_to_field(o: Any) -> PsychField:
    """An extracted objection Signal as a PsychField. A customer-quoted signal is a
    ``stated`` read; a studio-quoted / thread-shape signal (blocked_by_prereq, went-
    quiet) is ``inferred`` — its evidence is still a verbatim span of the real thread,
    but it is never asserted as the customer's own words."""
    stated = getattr(o, "source", "customer") == "customer"
    return PsychField(value=o.value, signal=STATED if stated else INFERRED,
                      evidence=o.evidence, evidence_source=SRC_CONVERSATION)


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
    return _objection_to_field(objs[0]), [_objection_to_field(o) for o in objs[1:]]


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
    # A trust breach from a bad prior experience (trust_concern) reads low exactly like
    # a first-timer trust objection — both are the customer's own evidenced words.
    trust_obj = next((o for o in signals.objections
                      if o.value in (OBJECTION_TRUST, OBJECTION_TRUST_CONCERN)), None)
    if trust_obj is not None:
        sig = STATED if getattr(trust_obj, "source", "customer") == "customer" else INFERRED
        return PsychField(value="low", signal=sig, evidence=trust_obj.evidence,
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
    # Scan the CUSTOMER-quoted evidence spans only — a studio-quoted span (a prereq or
    # an unanswered ask) is never evidence for the customer's emotion.
    spans = [o.evidence for o in signals.objections
             if getattr(o, "source", "customer") == "customer"]
    spans += [s.evidence for s in signals.styles]
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
        decision_blockers=[_objection_to_field(o) for o in signals.objections],
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
    OBJECTION_TRUST_CONCERN: (
        "acknowledge the past experience (without restating it) with a direct-artist "
        "commitment, a no-reschedule guarantee, and a manager point of contact — "
        "never a hard sell"),
    OBJECTION_BLOCKED_PREREQ: (
        "help with the prerequisite first (e.g. laser removal guidance) — a next-step "
        "note, explicitly not a discount pitch"),
    OBJECTION_WENT_QUIET: (
        "a low-pressure pick-up-where-we-left-off at the exact step the thread stopped"),
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


def _trait_filter_field(
    name: str, field: PsychField, profile: PsychProfile,
    allowed: frozenset[str], fp_corpus: str,
) -> PsychField:
    """The SENSITIVE-TRAIT gate for one surviving field (spec §7/§24). If the field's
    value or evidence asserts a protected trait that is neither derivable from a
    first-party field the customer provided (e.g. age from a real ``dob``) nor the
    customer's own verbatim words, the WHOLE field is dropped to insufficient-signal
    and the drop is recorded honestly on ``profile.trait_filtered``."""
    if field.signal == INSUFFICIENT:
        return field
    viols = trait_violations(
        f"{field.value}\n{field.evidence}", allowed=allowed, first_party_corpus=fp_corpus
    )
    if not viols:
        return field
    cats = sorted({v.category for v in viols})
    profile.trait_filtered.append({
        "field": name,
        "categories": ", ".join(cats),
        "matched": ", ".join(sorted({v.span.strip().lower() for v in viols}))[:120],
        "note": "protected-trait assertion not backed by customer-provided data; dropped",
    })
    return PsychField.insufficient(
        f"protected trait ({', '.join(cats)}) filtered — not customer-provided"
    )


def _apply_trait_filter(
    profile: PsychProfile, allowed: frozenset[str], fp_corpus: str,
) -> None:
    """Run the protected-traits gate over every SURVIVING field (scalars + lists),
    after corpus validation and before the recount/narrative — so a filtered read can
    never leak into ``where_customer_sits`` / ``best_reengagement_angle``."""
    for name, field in profile.scalar_fields():
        setattr(profile, name, _trait_filter_field(name, field, profile, allowed, fp_corpus))
    profile.secondary_objections = [
        v for v in (
            _trait_filter_field("secondary_objections", o, profile, allowed, fp_corpus)
            for o in profile.secondary_objections
        )
        if v.signal != INSUFFICIENT
    ]
    profile.decision_blockers = [
        v for v in (
            _trait_filter_field("decision_blockers", b, profile, allowed, fp_corpus)
            for b in profile.decision_blockers
        )
        if v.signal != INSUFFICIENT
    ]


def _finalize(profile: PsychProfile, corpus: str, present: set[str],
              signals: ReasonSignals, *,
              allowed: frozenset[str] = frozenset(), fp_corpus: str = "") -> PsychProfile:
    """Run the anti-fabrication gate over every field, then the protected-traits gate
    over what survived, recount grounding, and compose the derived one-liners from
    what remains."""
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

    # SENSITIVE-TRAIT BAN (spec §7/§24): drop any surviving read that asserts a
    # protected trait the customer did not provide, recording each drop honestly.
    _apply_trait_filter(profile, allowed, fp_corpus)

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


def _merge_llm(
    base: PsychProfile, llm: "PsychLLMOut", customer_corpus: str = ""
) -> PsychProfile:
    """Overlay an LLM read onto the deterministic floor field-by-field, but ONLY where the
    LLM produced a grounded, in-vocabulary read; otherwise the deterministic field stands.
    (Validation of evidence-vs-corpus happens afterwards in :func:`_finalize`.)

    S2 MISCLASSIFICATION GUARD (the Oscar Diaz regression): when the deterministic
    floor found a STATED primary objection (the customer's own words), the LLM may
    refine it WITHIN the same taxonomy family (payment -> price, trust ->
    trust_concern, …) or add secondaries — but it may NOT flip the primary to a
    DIFFERENT family on merely on-vocabulary evidence: the flip is accepted only when
    the LLM's evidence span is CUSTOMER-quoted (it literally appears in
    ``customer_corpus``, the normalized text of the customer's own turns — a
    studio-quoted line can never re-diagnose the customer's stated objection). A
    refused flip keeps the deterministic primary; the LLM's proposal survives as a
    secondary (still subject to the corpus gate in :func:`_finalize`)."""
    def pick(base_field: PsychField, llm_field: PsychField | None, allowed: frozenset[str]) -> PsychField:
        if llm_field is None:
            return base_field
        clamped = _clamp(llm_field, allowed)
        if clamped.signal == INSUFFICIENT or not clamped.value:
            return base_field
        return clamped

    det_primary = base.primary_objection  # the deterministic floor's read (pre-overlay)
    base.umbrella_category = pick(base.umbrella_category, llm.umbrella_category, _CATEGORIES)
    base.primary_objection = pick(base.primary_objection, llm.primary_objection, _OBJECTIONS)
    if (
        det_primary.signal == STATED
        and det_primary.value
        and base.primary_objection is not det_primary
        and objection_family(base.primary_objection.value)
        != objection_family(det_primary.value)
    ):
        proposed = base.primary_objection
        ev = _norm(proposed.evidence)
        customer_quoted = bool(ev) and ev in (customer_corpus or "")
        if not customer_quoted:
            # Refuse the cross-family flip; keep the customer's stated objection as
            # primary and demote the proposal to a secondary (never silently lost).
            if proposed.value and proposed.value not in [
                x.value for x in base.secondary_objections
            ] + [det_primary.value]:
                base.secondary_objections.append(proposed)
            base.primary_objection = det_primary
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
        "and the objection taxonomy (price/timing/trust/uncertainty/payment/"
        "trust_concern/blocked_by_prereq/went_quiet_mid_booking/none-found) for "
        "objections. trust_concern = a bad prior experience with US (reschedules, "
        "refund/dispute language, 'no longer confident'). blocked_by_prereq = a concrete "
        "prerequisite stops the booking (laser removal, healing, a consult first) — "
        "quote the thread line stating it. went_quiet_mid_booking = they were actively "
        "booking and stopped responding at a concrete step — quote that exact "
        "unanswered ask.\n\n"
        "HARD RULES (a fabrication is a critical failure):\n"
        "- For EVERY dimension set signal to 'stated' ONLY if the customer's own words "
        "evidence it, and put that VERBATIM quote in 'evidence' with evidence_source="
        "'conversation'. Set 'inferred' if you derive it from a CRM/persona field (put "
        "the field in 'evidence', source 'csv'/'persona'/'tattoo_history'/'memory'). Set "
        "'insufficient-signal' and leave value empty if there is NO real evidence.\n"
        "- NEVER invent an objection, motive, urgency, or emotion the data does not show. "
        "When unsure, choose 'insufficient-signal'. Honest gaps beat confident guesses.\n"
        "- PROTECTED TRAITS — ABSOLUTE BAN (compliance): NEVER read, infer, guess, or "
        "mention the customer's gender, age, ethnicity/race, health/disability, "
        "religion, sexuality, financial status or distress, immigration status, or "
        "political views — not in any value, evidence, or note, not even hedged "
        "('probably', 'seems'), and not from their name, photo, handle, or writing "
        "style. A price objection is about THIS purchase, never about their finances "
        "as a person. A deterministic post-filter drops any output asserting these; "
        "do not produce it."
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
    mems = [
        str((m or {}).get("text") or "").strip()
        for m in (facts.get("memories") or [])
        if isinstance(m, dict)
    ]
    mems = [m for m in mems if m]
    if mems:
        lines.append("\n# PRIOR CAMPAIGN MEMORIES (our own CRM records about this lead — "
                     "real evidence, evidence_source='memory'):")
        lines.extend(f"- {m}" for m in mems)
    if social:
        lines.append(f"\n# SOCIAL SIGNAL (verbatim, already trait-filtered): {social}")
    lines.append("\nProduce the structured psychological read. Ground every 'stated' read "
                 "in a verbatim quote above; use 'insufficient-signal' wherever there is no "
                 "real evidence.")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Public entry point.
# --------------------------------------------------------------------------- #
def _social_fetch_enabled() -> bool:
    """Whether the analyst may make the ONE consent-safe Firecrawl call for a lead's
    customer-provided handle. OFF by default (per-lead live egress is opt-in):
    enabled by ``STUDIO_SOCIAL_RESEARCH`` or, absent that, by the existing
    ``STUDIO_DEEP_RESEARCH`` opt-in (deep per-lead research implies the consent-safe
    social lookup). Keyless environments degrade honestly inside the fetcher."""
    raw = os.environ.get("STUDIO_SOCIAL_RESEARCH")
    if raw is not None:
        return raw.strip().lower() in ("1", "true", "yes", "on")
    raw = os.environ.get("STUDIO_DEEP_RESEARCH")
    if raw is not None:
        return raw.strip().lower() in ("1", "true", "yes", "on")
    return False


def analyze_customer(
    facts: dict[str, Any],
    conversation: list[dict[str, Any]] | dict[str, Any] | None = None,
    social: str | None = None,
    *,
    known_artists: list[str] | None = None,
    use_llm: bool | None = None,
    fetch_social: bool | None = None,
) -> PsychProfile:
    """Analyze ONE customer into a deep, evidence-grounded :class:`PsychProfile`.

    ``facts`` is the lead's real grounded facts (as ``customer_research.lookup_lead``
    returns). ``conversation`` is either a list of ``{speaker,text}`` turns or the
    ``conversations.get_conversation`` dict (``{turns,...}``). ``social`` is any verbatim
    social snippet — when the caller passes none and the lead's OWN row carries a
    customer-provided ``ig_handle`` / ``linkedin_handle``, the analyst fetches public
    profile context itself via :func:`studio.customer_research.gather_social_context`
    (one gated Firecrawl call; ``fetch_social`` overrides the env gate, default off;
    NEVER name-based discovery). Any social text — passed or fetched — is scrubbed by
    the protected-traits filter before it becomes evidence.

    A deterministic, fully-grounded read is always computed (the keyless floor). When an
    LLM is available (and not disabled) it enriches the read, but its output is passed
    through the SAME corpus-validation gate — no read survives that the customer's own
    data does not evidence — and then the SENSITIVE-TRAIT gate (spec §7/§24): any read
    asserting a protected trait the customer did not provide is dropped and recorded on
    ``profile.trait_filtered``. Returns the profile; every field is tagged stated/
    inferred/insufficient-signal and carries its evidence span. NEVER fabricates."""
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

    # First-party exemption substrate for the protected-traits gate: the customer's own
    # words/fields (+ our CRM memories), NEVER web/social text or the generated persona.
    allowed = allowed_categories(facts)
    fp_corpus = build_first_party_corpus(facts, turns)

    # CONSENT-SAFE social context (spec §7): only when the lead row itself carries a
    # customer-provided handle, only when explicitly enabled, and only via the vetted
    # Firecrawl provider. A blocked host / proxy / keyless env degrades to honest-None.
    trait_drops: list[dict[str, str]] = []
    if social is None and (facts.get("ig_handle") or facts.get("linkedin_handle")):
        do_fetch = _social_fetch_enabled() if fetch_social is None else bool(fetch_social)
        if do_fetch:
            try:
                from studio.customer_research import gather_social_context

                social = gather_social_context(facts, enabled=True)
            except Exception:
                social = None  # honest-none; never a fabricated social signal
    if social:
        # Everything extracted (or passed) from social surfaces passes the sensitive-
        # trait filter BEFORE it can enter the corpus, the prompt, or any evidence.
        clean, drops = filter_lines(str(social), allowed=allowed,
                                    first_party_corpus=fp_corpus)
        for d in drops:
            trait_drops.append({
                "field": "social",
                "categories": d.get("categories", ""),
                "matched": d.get("matched", ""),
                "note": "protected-trait line scrubbed from social context",
            })
        social = clean or None

    signals = extract_signals(turns, known_artists=known_artists)
    corpus = _build_corpus(facts, signals, social, turns)
    present = _present_sources(facts, signals, social)

    # CUSTOMER-only corpus (S2 guard): the normalized text of the customer's OWN
    # turns — the only evidence that can justify the LLM flipping a STATED primary
    # objection to a different taxonomy family (a studio-quoted line never can).
    from studio.conversations import SPEAKER_CUSTOMER, normalize_turns

    customer_corpus = _norm(
        "\n".join(
            t["text"] for t in normalize_turns(turns) if t["speaker"] == SPEAKER_CUSTOMER
        )
    )

    profile = _deterministic_profile(facts, signals, social)
    profile.trait_filtered.extend(trait_drops)

    do_llm = _llm_enabled() if use_llm is None else use_llm
    if do_llm:
        try:
            cell = _build_psych_cell()
            out = cell.run_sync(_build_psych_prompt(facts, turns, social))
            profile = _merge_llm(profile, out, customer_corpus)
        except Exception:
            # Any cell/model failure: the deterministic floor stands (honest, grounded).
            profile.source = "deterministic"

    return _finalize(profile, corpus, present, signals,
                     allowed=allowed, fp_corpus=fp_corpus)
