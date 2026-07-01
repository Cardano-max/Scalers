"""The EXECUTABLE campaign plan — the blueprint the planner builds BEFORE drafting
(P1.5 blueprint #1: plan-first, planner/executor separation).

The interview captures INTENT as a :class:`studio.agui.CampaignPlan` (goal, audience,
channels, offer, target_category, scope, ...). That is NOT an execution plan. This
module decomposes that intent into a :class:`CampaignBlueprint` — a concrete, testable
plan the per-lead executor runs AGAINST:

  * ``targets`` — which lead cohort (from ``plan.target_category`` + ``plan.scope``),
  * ``per_channel_quota`` — how many drafts per channel (caps the fan-out),
  * ``artist_shop_rules`` — scope constraints (one artist / one shop / whole studio),
  * ``offer_logic`` — objection-type → REAL offer (grounded in :func:`studio.offers.get_offers`
    and passed through :func:`studio.offers.substantiate`; an objection with no real offer
    maps to ``None`` — NEVER an invented code),
  * ``research_questions`` — what the executor must answer per lead,
  * ``compliance_constraints`` — the gates that HOLD (approve-first, no-fabrication,
    exactly-once),
  * ``review_rules`` — what needs human approval,
  * ``stop_conditions`` — when to stop drafting (quota met / no more leads / contradiction).

The deterministic core needs no model — it is a pure decomposition of the plan + the
real offers doc, so it is unit-testable without a key or a network. An optional LLM
enrichment (routed to the best tier via :mod:`studio.model_routing`, with the stable
brand/offers/taxonomy prefix prompt-cached) refines the campaign ANGLE and prioritizes
the research questions — but it is held to the same no-fabrication discipline as
``psych_profile.py``: it may propose wording, it may NOT invent an offer, a number, or a
cohort. When it does not run (no key / disabled / error) the blueprint is honest about
it: ``planner_model`` reads ``grounded_rules`` (not a fake Opus).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from studio.model_routing import (
    PLANNER_MODEL,
    build_cached_prompt,
    cached_anthropic_settings,
)

# The objection taxonomy the offer_logic is keyed on (mirrors reason_history's set +
# the two lifecycle objections select_offer answers). Kept explicit so a reviewer sees
# exactly which objections the plan considered.
_OBJECTION_TAXONOMY: tuple[str, ...] = (
    "price",
    "payment",
    "timing",
    "trust",
    "uncertainty",
    "reactivation",
    "recurring",
)

# target_category → the objection the plan ASSUMES dominates that cohort. This is the
# claim the progress-aware replanning hook (blueprint #3) tests against the analyst's
# measured objections — a real contradiction (measured ≠ assumed) triggers a replan.
_CATEGORY_ASSUMED_OBJECTION: dict[str, str] = {
    "past-customer-reactivation": "price",
    "reactivation": "price",
    "converted-but-unpaid": "payment",
    "unpaid": "payment",
    "recurring-customer": "recurring",
    "recurring": "recurring",
}

# campaign_type → assumed dominant objection (fallback when target_category is unset).
_CAMPAIGN_TYPE_ASSUMED_OBJECTION: dict[str, str] = {
    "winback": "price",
    "win-back": "price",
    "reactivation": "price",
    "flash": "timing",
    "payment": "payment",
    "loyalty": "recurring",
    "retention": "recurring",
}


def resolve_assumed_objection(plan: Any) -> str | None:
    """The objection the plan ASSUMES dominates the cohort, resolved (in priority order)
    from an explicit ``plan.assumed_objection``, then ``target_category``, then
    ``campaign_type``. ``None`` when nothing implies one — the replan then cannot fire
    (no recorded assumption to contradict). Pure; used by both the planner and the plan
    write-back."""
    explicit = (getattr(plan, "assumed_objection", "") or "").strip().lower()
    if explicit:
        return explicit
    category = (getattr(plan, "target_category", "") or "").strip().lower()
    if category in _CATEGORY_ASSUMED_OBJECTION:
        return _CATEGORY_ASSUMED_OBJECTION[category]
    ctype = (getattr(plan, "campaign_type", "") or "").strip().lower()
    return _CAMPAIGN_TYPE_ASSUMED_OBJECTION.get(ctype)


class TargetCohort(BaseModel):
    """Which leads this campaign targets — the cohort the executor pulls from."""

    category: str = "all"
    scope: str = "whole studio"
    description: str = ""
    estimated_size: int | None = None


class OfferRule(BaseModel):
    """One objection → REAL offer mapping. ``offer_code`` is ``None`` (with a note) when
    no substantiated offer answers this objection — never an invented code."""

    objection: str
    offer_code: str | None = None
    offer_kind: str | None = None
    substantiated: bool = False
    note: str = ""


class StopConditions(BaseModel):
    """When the executor stops fanning out drafts."""

    total_quota: int = 0
    per_channel_quota: dict[str, int] = Field(default_factory=dict)
    stop_on_no_more_leads: bool = True
    stop_on_contradiction: bool = True
    notes: list[str] = Field(default_factory=list)


class CampaignBlueprint(BaseModel):
    """The executable plan the supervisor builds BEFORE the per-lead draft loop.

    Distinct from :class:`studio.agui.CampaignPlan` (the interview INTENT) — this is the
    decomposed, testable execution plan the per-lead loop runs against."""

    run_id: str | None = None
    campaign_id: str | None = None
    tenant_id: str = ""
    goal: str = ""
    angle: str = ""
    targets: TargetCohort = Field(default_factory=TargetCohort)
    per_channel_quota: dict[str, int] = Field(default_factory=dict)
    artist_shop_rules: list[str] = Field(default_factory=list)
    offer_logic: list[OfferRule] = Field(default_factory=list)
    # The objection the plan assumes dominates the cohort — the replan hook checks it.
    assumed_dominant_objection: str | None = None
    research_questions: list[str] = Field(default_factory=list)
    compliance_constraints: list[str] = Field(default_factory=list)
    review_rules: list[str] = Field(default_factory=list)
    stop_conditions: StopConditions = Field(default_factory=StopConditions)
    # Honest provenance: the tier the planner ACTUALLY ran at. ``grounded_rules`` when
    # the deterministic core built the plan with no model call; the Opus pin when the
    # LLM enrichment made a real call.
    planner_model: str = "grounded_rules"
    planner_rationale: str = ""


class _BlueprintEnrichment(BaseModel):
    """The narrow, text-only output the optional planner LLM may return. It refines
    WORDING/PRIORITY only — it cannot introduce an offer, a number, or a cohort."""

    angle: str = ""
    prioritized_research_questions: list[str] = Field(default_factory=list)
    rationale: str = ""


# --------------------------------------------------------------------------- #
# Deterministic decomposition helpers (pure — no model, no network).
# --------------------------------------------------------------------------- #
def _default_channels(plan_channels: list[str] | None) -> list[str]:
    """The channels to plan quota for — the operator's chosen channels, else empty
    (honest: a plan with no channel has no quota, and the summary says so)."""
    return [c.strip() for c in (plan_channels or []) if c and c.strip()]


def _distribute_quota(total: int, channels: list[str]) -> dict[str, int]:
    """Split ``total`` drafts across ``channels`` as evenly as possible (remainder to the
    earliest channels). Empty channels or non-positive total → empty (honest no quota)."""
    if total <= 0 or not channels:
        return {}
    base, extra = divmod(total, len(channels))
    return {c: base + (1 if i < extra else 0) for i, c in enumerate(channels)}


def _artist_shop_rules(scope: str) -> list[str]:
    """Scope constraints from ``plan.scope`` — real, enforceable rules the executor
    honors when resolving the cohort. Unknown/empty scope → the studio-wide default."""
    s = (scope or "").strip().lower()
    if "artist" in s:
        return [
            "Target ONLY leads tied to a single artist (scope=one artist).",
            "Each draft references that artist's own work — never another artist's.",
        ]
    if "shop" in s or "location" in s:
        return [
            "Target ONLY leads at a single shop/location (scope=one shop).",
            "Offers and availability are scoped to that shop.",
        ]
    return [
        "Whole-studio scope: any active lead is eligible.",
        "Offers apply studio-wide; artist attribution used only where the lead has one.",
    ]


def _target_description(category: str, scope: str) -> str:
    labels = {
        "open-warm-lead": "open warm leads (inquired, not yet booked)",
        "artist-specific-warm-lead": "warm leads attached to a specific artist",
        "converted-but-unpaid": "booked-but-unpaid customers (deposit/payment pending)",
        "recurring-customer": "recurring customers (repeat bookings)",
        "past-customer-reactivation": "lapsed past customers to reactivate",
        "all": "all eligible leads for the studio",
        "": "all eligible leads for the studio",
    }
    base = labels.get((category or "").strip().lower(), f"cohort '{category}'")
    return f"{base}; scope: {scope or 'whole studio'}"


def _build_offer_logic(tenant_id: str, dsn: str | None) -> list[OfferRule]:
    """Objection → REAL substantiated offer, for the objections that HAVE one.

    Built ONLY from :func:`studio.offers.get_offers`; each chosen code is re-checked by
    :func:`studio.offers.substantiate` (the fail-closed gate) at PLAN time. The list holds
    ONLY objections with a real substantiated offer — an objection with no real offer is
    simply ABSENT (``offer_rule_for`` then returns None → the draft references NO discount).
    ``get_offers() == []`` ⇒ ``offer_logic == []`` (no staged action can reference a code).
    A store hiccup yields ``[]`` — never a fabricated offer."""
    from studio.offers import get_offers, select_offer, substantiate

    try:
        offers = get_offers(tenant_id, dsn=dsn)
    except Exception:
        offers = []
    if not offers:
        return []

    rules: list[OfferRule] = []
    for objection in _OBJECTION_TAXONOMY:
        chosen = select_offer(offers, objection=objection, interest=None)
        if chosen is not None:
            chosen = substantiate(offers, chosen.code)  # fail-closed gate at plan time
        if chosen is None:
            continue  # no real offer for this objection -> not in the plan (never invented)
        rules.append(
            OfferRule(
                objection=objection,
                offer_code=chosen.code,
                offer_kind=chosen.kind,
                substantiated=True,
                note=chosen.as_evidence(),
            )
        )
    return rules


_RESEARCH_QUESTIONS = [
    "What is this lead's real history (city, past tattoos, interests, lifecycle stage)?",
    "What does this lead's own conversation evidence as their primary objection (if any)?",
    "Where does this lead sit on the buyer-readiness ladder, grounded in their data?",
    "What is the best re-engagement angle supported by this lead's evidence?",
    "Is there a REAL substantiated offer that answers this lead's objection?",
]

_COMPLIANCE_CONSTRAINTS = [
    "HELD / approve-first: every draft is staged PENDING; nothing sends in this slice.",
    "No fabrication: psychology, offers, numbers, and sources must trace to real evidence.",
    "Offers fail closed: an unsubstantiated offer code never reaches a draft.",
    "Exactly-once: each lead is drafted once (idempotency_key = run_id:customer_id).",
]

_REVIEW_RULES = [
    "Any send requires explicit human approval (approve-first at the Review Queue).",
    "A draft the critic flags for revision/rejection is surfaced with its low confidence.",
    "A measured contradiction against the plan is recorded for the operator to review.",
]


def offer_rule_for(blueprint: CampaignBlueprint, objection: str | None) -> OfferRule | None:
    """The blueprint's offer rule for an objection (or ``None`` if the objection is empty
    / not in the plan). The executor consults this to decide whether an offer is PERMITTED
    for a lead's objection — the plan, not ad-hoc code, governs the offer decision."""
    if not objection:
        return None
    want = objection.strip().lower()
    for rule in blueprint.offer_logic:
        if rule.objection == want:
            return rule
    return None


def build_blueprint(
    plan: Any,
    tenant_id: str,
    dsn: str | None,
    *,
    run_id: str | None = None,
    campaign_id: str | None = None,
    session_id: str | None = None,
    strategist_angle: str | None = None,
    use_llm: bool | None = None,
) -> CampaignBlueprint:
    """THE PLANNER. Decompose the interview intent (``plan``: a CampaignPlan) into an
    executable :class:`CampaignBlueprint`.

    Deterministic core (always): targets/quota/scope-rules/offer-logic/research/
    compliance/review/stop, grounded in the plan + the REAL offers doc. Optional LLM
    enrichment (best tier, prompt-cached stable prefix) refines the angle + prioritizes
    the research questions, held to the same no-fabrication gate; it never introduces an
    offer/number/cohort. ``use_llm`` forces enrichment on/off; ``None`` = auto (on iff a
    key is present). Pure/offline when ``use_llm`` is False."""
    goal = (getattr(plan, "goal", "") or "").strip() or "win back lapsed clients"
    category = (getattr(plan, "target_category", "") or "").strip() or "all"
    scope = (getattr(plan, "scope", "") or "").strip() or "whole studio"
    channels = _default_channels(getattr(plan, "channels", None))
    total_quota = int(
        getattr(plan, "output_count", 0) or getattr(plan, "lead_count", 0) or 0
    )
    per_channel = _distribute_quota(total_quota, channels)

    targets = TargetCohort(
        category=category,
        scope=scope,
        description=_target_description(category, scope),
        estimated_size=(total_quota or None),
    )
    assumed = resolve_assumed_objection(plan)

    stop = StopConditions(
        total_quota=total_quota,
        per_channel_quota=per_channel,
        notes=[
            "Stop when the per-channel quota is met.",
            "Stop when the cohort is exhausted (no more eligible leads).",
            "Flag + stop-to-replan when measured evidence contradicts the plan.",
        ],
    )

    blueprint = CampaignBlueprint(
        run_id=run_id,
        campaign_id=campaign_id,
        tenant_id=tenant_id,
        goal=goal,
        angle=(strategist_angle or "").strip() or goal,
        targets=targets,
        per_channel_quota=per_channel,
        artist_shop_rules=_artist_shop_rules(scope),
        offer_logic=_build_offer_logic(tenant_id, dsn),
        assumed_dominant_objection=assumed,
        research_questions=list(_RESEARCH_QUESTIONS),
        compliance_constraints=list(_COMPLIANCE_CONSTRAINTS),
        review_rules=list(_REVIEW_RULES),
        stop_conditions=stop,
        planner_model="grounded_rules",
        planner_rationale=_deterministic_rationale(category, scope, total_quota, channels),
    )

    if _enrichment_enabled(use_llm):
        _enrich_with_planner_llm(blueprint, plan)

    return blueprint


def _deterministic_rationale(
    category: str, scope: str, total_quota: int, channels: list[str]
) -> str:
    return (
        f"Deterministic decomposition of the interview intent: target '{category}' "
        f"(scope {scope}); quota {total_quota or 'unset'} across "
        f"{', '.join(channels) or 'no channel chosen'}; offer_logic grounded in the "
        "real offers doc (no invented codes)."
    )


def _enrichment_enabled(use_llm: bool | None) -> bool:
    """Auto policy: explicit ``use_llm`` wins; ``None`` enables enrichment only when an
    Anthropic key is present AND we are not under pytest — so the unit suite stays
    offline/deterministic (no real Opus call, ``planner_model`` honestly reads
    ``grounded_rules``) while a real run (not under pytest) routes the planner to the best
    tier for real."""
    if use_llm is not None:
        return use_llm
    import os

    if os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _stable_context(blueprint: CampaignBlueprint) -> str:
    """The STABLE prompt prefix (brand/offers/taxonomy/compliance) that is prompt-cached.
    Deterministic string so the Anthropic cache key is stable across runs of a tenant."""
    offers = "\n".join(
        f"- {r.objection}: {r.offer_code or 'NONE'}"
        + (f" ({r.note})" if r.offer_code else "")
        for r in blueprint.offer_logic
    )
    return (
        "You are the campaign PLANNER for a tattoo studio marketing engine. You refine an "
        "already-decomposed execution blueprint. HARD RULES: never invent an offer code, a "
        "number, a source, or a cohort; only re-word the angle and prioritize the given "
        "research questions.\n\n"
        "SUBSTANTIATED OFFER LOGIC (objection → real offer; NONE means no discount is "
        f"allowed for that objection):\n{offers}\n\n"
        "COMPLIANCE (always holds):\n"
        + "\n".join(f"- {c}" for c in blueprint.compliance_constraints)
        + "\n\nCANDIDATE RESEARCH QUESTIONS:\n"
        + "\n".join(f"- {q}" for q in blueprint.research_questions)
    )


def _enrich_with_planner_llm(blueprint: CampaignBlueprint, plan: Any) -> None:
    """Best-tier (Opus) planner pass: refine the angle + prioritize research questions,
    with the stable brand/offers/taxonomy prefix prompt-cached. Mutates ``blueprint`` in
    place on success (angle/research_questions/planner_rationale + planner_model=Opus).

    Held to the no-fabrication gate: the model returns text only (an angle + a REORDER of
    the given questions); it cannot introduce an offer or number. On ANY failure the
    blueprint is left as the deterministic plan and ``planner_model`` stays
    ``grounded_rules`` — never a fake Opus attribution for a call that did not happen."""
    try:
        from pydantic_ai import Agent

        volatile = (
            f"CAMPAIGN GOAL: {blueprint.goal}\n"
            f"TARGET COHORT: {blueprint.targets.description}\n"
            f"CHANNELS + QUOTA: {blueprint.per_channel_quota or 'none chosen'}\n"
            f"SCOPE RULES: {'; '.join(blueprint.artist_shop_rules)}\n"
            f"ASSUMED DOMINANT OBJECTION: {blueprint.assumed_dominant_objection or 'none assumed'}\n\n"
            "Return: a sharp one-sentence campaign ANGLE grounded ONLY in the above (no "
            "invented offer/number), and the research questions reordered by priority for "
            "this cohort. Do not add or remove a question; do not name an offer that is "
            "NONE above."
        )
        stable = _stable_context(blueprint)
        agent: Agent = Agent(
            PLANNER_MODEL,
            output_type=_BlueprintEnrichment,
            model_settings=cached_anthropic_settings(
                temperature=0.0, model=PLANNER_MODEL, stable_context=stable
            ),
            defer_model_check=True,
        )
        # Caching is applied ONLY when the stable prefix clears the model minimum (the seam
        # guards against net-negative caching of a small planner prefix).
        prompt = build_cached_prompt(stable, volatile, PLANNER_MODEL)
        result = agent.run_sync(prompt)
        out = result.output
        if out.angle.strip():
            blueprint.angle = out.angle.strip()
        # Only accept a reprioritization that is a permutation of the real questions —
        # never let the model inject a fabricated question.
        allowed = {q.strip() for q in blueprint.research_questions}
        reordered = [q.strip() for q in out.prioritized_research_questions if q.strip() in allowed]
        if reordered:
            # keep any questions the model dropped, appended in original order (no loss)
            tail = [q for q in blueprint.research_questions if q.strip() not in set(reordered)]
            blueprint.research_questions = reordered + tail
        blueprint.planner_rationale = (
            out.rationale.strip() or blueprint.planner_rationale
        )
        blueprint.planner_model = PLANNER_MODEL
    except Exception:
        # Deterministic plan stands; honest provenance retained.
        return
