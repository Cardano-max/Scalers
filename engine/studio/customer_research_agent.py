"""customer_research_agent — the REAL per-lead RESEARCH AGENT (CustomerAcq-nmh.7).

Spec §7/§24: for each lead, research from the data ACTUALLY AVAILABLE — uploaded CSV
fields, prior conversation, CRM columns, brand/customer notes, and any
customer-provided social handle — into a structured read: possible interests, style
preference, tattoo-related signals, business/profile context, MISSING data, and a
confidence level. It NEVER fabricates (no invented likes/objections/budget) and NEVER
infers a protected/sensitive attribute (gender, age, ethnicity, health, religion,
sexuality, financial status). When the data is thin it says so — honest
``personalization=low`` beats fake depth.

GATED EXTENSION: real web / public-social research (Firecrawl / public IG-FB) is a
capability that may run ONLY once a web-research skill is **REGISTERED-IN-USE** in
``docs/skills/registry.md`` (the sec supply-chain gate). Until then :func:`research_customer`
leaves the public-research path INERT and records an honest "no public research run"
note — the seam is present so a later sec sign-off lights it up with no gate-logic change.

Pure/deterministic and DB-free (it reads facts the caller already resolved); the psych/
conversation signal extraction reuses :func:`studio.reason_history.extract_signals`.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from studio.reason_history import extract_signals

# Web-research capabilities are gated on ONE of these skills being REGISTERED-IN-USE.
_WEB_RESEARCH_SKILLS: frozenset[str] = frozenset({
    "where-your-customer-lives", "map-your-market", "competitor-pr-finder",
    "customer-research", "web-research", "firecrawl-research", "public-social-research",
})
_REGISTERED_STATUS = "registered-in-use"

# The §7 fields we account for as present-or-MISSING (the "say missing" contract).
_RESEARCH_FIELDS = (
    "known_interests", "style_preference", "tattoo_signals", "business_context",
    "conversation_signals", "social_handle",
)


class ResearchField(BaseModel):
    """One research finding with provenance. ``confidence`` grades the signal
    (high=first-party CSV/conversation; medium=persona-inferred; low=weak/derived;
    none=absent). ``source`` names the real origin; ``evidence`` is the verbatim span /
    field it rests on. Never a fabricated value."""

    value: Any = None
    confidence: str = "none"  # high | medium | low | none
    source: str = "none"      # csv | conversation | persona | tattoo_history | notes | none
    evidence: str = ""

    @property
    def present(self) -> bool:
        return self.value not in (None, "", [], {}) and self.confidence != "none"


class CustomerResearch(BaseModel):
    """The structured per-lead research read (spec §7). Every field traces to real,
    available data or is honestly empty/MISSING; the public-research block is inert until
    the sec skill-registry gate opens."""

    customer_id: str | None = None
    interests: list[str] = Field(default_factory=list)
    interest_evidence: list[str] = Field(default_factory=list)
    style_preference: ResearchField = Field(default_factory=ResearchField)
    tattoo_signals: list[str] = Field(default_factory=list)
    business_context: ResearchField = Field(default_factory=ResearchField)
    missing_data: list[str] = Field(default_factory=list)
    confidence_level: str = "low"  # low | medium | high
    # Gated web / public-social research — inert (ran=False) until a web-research skill
    # is REGISTERED-IN-USE; ``sources`` stays [] and ``note`` explains why.
    public_research: dict[str, Any] = Field(
        default_factory=lambda: {"ran": False, "note": "", "sources": []}
    )

    def summary_line(self) -> str:
        """A short honest one-liner for the dossier's public_research_summary."""
        bits: list[str] = []
        if self.interests:
            bits.append("interests: " + ", ".join(self.interests[:4]))
        if self.style_preference.present:
            bits.append(f"style: {self.style_preference.value}")
        if self.business_context.present:
            bits.append(self.business_context.value)
        if not bits:
            return ""
        return f"Research ({self.confidence_level} confidence) — " + "; ".join(bits) + "."


def _registry_path() -> Path | None:
    """Locate ``docs/skills/registry.md`` — an explicit env override, else walk up from
    this module until a repo with ``docs/skills/registry.md`` is found."""
    override = os.environ.get("SCALERS_SKILL_REGISTRY")
    if override:
        p = Path(override)
        return p if p.is_file() else None
    here = Path(__file__).resolve()
    for parent in here.parents:
        cand = parent / "docs" / "skills" / "registry.md"
        if cand.is_file():
            return cand
    return None


def web_research_allowed(dsn: str | None = None) -> bool:
    """Whether a web/public-social research skill is **REGISTERED-IN-USE** in the sec
    registry — the ONLY condition under which the web path may run (nmh.7). Fail-CLOSED:
    an unreadable/absent registry, or a row that is IN-VETTING/ELIGIBLE/HELD/REJECTED,
    returns False (the path stays inert, honestly). Never loads or runs any skill here —
    it only reads the registry status."""
    path = _registry_path()
    if path is None:
        return False
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return False
    for line in text.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        skill = cells[0].lower()
        status = cells[-1].lower()
        if skill in _WEB_RESEARCH_SKILLS and status == _REGISTERED_STATUS:
            return True
    return False


def _business_context(facts: dict[str, Any]) -> ResearchField:
    """The lead's business/profile context from REAL CRM fields — customer type /
    lifecycle / a note. Never inferred beyond what a field states."""
    ctype = str(facts.get("customer_type") or "").strip()
    if ctype:
        return ResearchField(value=f"{ctype} customer", confidence="high",
                             source="csv", evidence=f"customer_type={ctype}")
    traits = facts.get("persona_traits", {}) or {}
    lifecycle = str(traits.get("lifecycle_stage") or "").strip()
    if lifecycle:
        label = {"lapsing": "win-back candidate", "lead-no-visit": "lead (no visit yet)",
                 "churn-risk": "at-risk / win-back", "recurring": "recurring customer",
                 "loyal": "loyal recurring customer"}.get(lifecycle, lifecycle)
        return ResearchField(value=label, confidence="medium", source="persona",
                             evidence=f"lifecycle_stage={lifecycle}")
    if traits.get("win_back_candidate"):
        return ResearchField(value="win-back candidate", confidence="medium",
                             source="persona", evidence="win_back_candidate=true")
    return ResearchField()


def research_customer(
    facts: dict[str, Any],
    conversation: list[dict[str, Any]] | dict[str, Any] | None = None,
    *,
    dsn: str | None = None,
    allow_web: bool | None = None,
) -> CustomerResearch:
    """Research ONE lead into a :class:`CustomerResearch` from the AVAILABLE data only.

    ``facts`` is the lead's grounded facts (``customer_research.lookup_lead`` shape).
    ``conversation`` is the stored thread (list of turns or the ``get_conversation``
    dict). Deterministic; no DB, no model, no skill load. The web/public-social path is
    inert unless ``allow_web`` is requested AND :func:`web_research_allowed` (the sec
    gate) is open — otherwise an honest "no public research run" note. NEVER fabricates a
    like/objection/trait; NEVER infers a sensitive attribute."""
    # Normalize the conversation input the same tolerant way the analyst does.
    if isinstance(conversation, dict):
        turns = conversation.get("turns") or []
    elif conversation is not None and hasattr(conversation, "turns"):
        turns = getattr(conversation, "turns") or []
    else:
        turns = conversation or []

    known_artists = [facts["artist"]] if facts.get("artist") else None
    signals = extract_signals(turns, known_artists=known_artists)

    # --- interests: real CSV interests + conversation-named styles (deduped) --------- #
    interests: list[str] = []
    interest_evidence: list[str] = []
    for i in facts.get("interests", []) or []:
        v = str(i).strip()
        if v and v.lower() not in [x.lower() for x in interests]:
            interests.append(v)
            interest_evidence.append(f"csv:interests={v}")
    for s in signals.styles:
        v = str(s.value).strip()
        if v and v.lower() not in [x.lower() for x in interests]:
            interests.append(v)
            interest_evidence.append(f"conversation:{v}")

    # --- style preference: past work > persona lean > conversation style ------------ #
    tattoos = facts.get("tattoo_history", []) or []
    last_style = tattoos[0].get("style") if tattoos and tattoos[0].get("style") else None
    traits = facts.get("persona_traits", {}) or {}
    if last_style:
        style_pref = ResearchField(value=last_style, confidence="high",
                                   source="tattoo_history",
                                   evidence=f"past_style={last_style}")
    elif signals.styles:
        s = signals.styles[0]
        style_pref = ResearchField(value=s.value, confidence="medium",
                                   source="conversation", evidence=s.evidence)
    elif traits.get("aesthetic_lean"):
        style_pref = ResearchField(value=str(traits["aesthetic_lean"]),
                                   confidence="medium", source="persona",
                                   evidence=f"aesthetic_lean={traits['aesthetic_lean']}")
    else:
        style_pref = ResearchField()

    # --- tattoo signals from the REAL conversation (styles/subjects/placements) ------ #
    tattoo_signals = [str(s.value) for s in signals.styles]

    business_context = _business_context(facts)

    # --- MISSING-data contract (§7/§24: say missing, never fake depth) --------------- #
    present = {
        "known_interests": bool(interests),
        "style_preference": style_pref.present,
        "tattoo_signals": bool(tattoo_signals),
        "business_context": business_context.present,
        "conversation_signals": bool(signals.has_conversation),
        "social_handle": bool(facts.get("ig_handle")),
    }
    missing_data = [f for f in _RESEARCH_FIELDS if not present[f]]

    # --- confidence: conversation grounds the strongest read ------------------------- #
    if signals.has_conversation and (tattoo_signals or signals.objections):
        confidence = "high"
    elif interests or style_pref.present or business_context.present:
        confidence = "medium"
    else:
        confidence = "low"

    # --- gated web / public-social path (inert until a skill is REGISTERED-IN-USE) --- #
    public_research = _run_public_research(facts, allow_web=allow_web, dsn=dsn)

    return CustomerResearch(
        customer_id=facts.get("customer_id"),
        interests=interests, interest_evidence=interest_evidence,
        style_preference=style_pref, tattoo_signals=tattoo_signals,
        business_context=business_context, missing_data=missing_data,
        confidence_level=confidence, public_research=public_research,
    )


def _run_public_research(
    facts: dict[str, Any], *, allow_web: bool | None, dsn: str | None
) -> dict[str, Any]:
    """The gated web/public-social research result. Inert (``ran=False``) unless the
    caller asks (``allow_web``) AND the sec registry gate is open; even then, with no
    live provider it degrades honestly to zero sources (never a fabricated post)."""
    if not allow_web:
        return {"ran": False, "sources": [],
                "note": "no public research run (web/social path not requested)"}
    if not web_research_allowed(dsn):
        return {
            "ran": False, "sources": [],
            "note": ("no public research run — no web/public-social research skill is "
                     "REGISTERED-IN-USE in docs/skills/registry.md (sec gate closed)"),
        }
    # Gate is open: attempt the real provider. It is keyless/absent here -> honest zero
    # sources (the real egress + provider wiring land with the sec-approved adapter).
    return {
        "ran": True, "sources": [],
        "note": "public research skill registered; no live provider result available",
    }
