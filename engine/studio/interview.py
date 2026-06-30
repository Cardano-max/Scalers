"""Deterministic interview gate for the Agency-page campaign run (P1a).

The Agency page must NOT launch orchestration blindly off a bare button. Before a
run may start the supervisor INTERVIEWS the operator to gather enough context. This
module is the pure, server-side state machine behind that gate: given the session's
:class:`~studio.agui.CampaignPlan` it computes which required fields are still
missing, the next question to ask, and whether enough context has been gathered to
ARM the run (the "I have enough context" point).

It is the Agency-tab counterpart to the voice GO-gate's ``plan_is_runnable``
(``studio.voice``) — richer, because the operator also has to agree on an output
count and a campaign type — and it NEVER trusts the client: arming is derived here
from the persisted plan. The supervisor (text or voice) may PHRASE the questions,
but whether the gate is ARMED is decided HERE, deterministically.

No model, no I/O — fully unit-testable. Field coercion is also here so the
``POST /studio/interview`` route stays thin.
"""

from __future__ import annotations

from typing import Any

# --------------------------------------------------------------------------- #
# Fields the interview collects. GATING fields must all be present before the run
# may arm; OPTIONAL fields are asked once gating is complete but never block.
# --------------------------------------------------------------------------- #

# (field, question) in the order the supervisor asks. These define a runnable
# campaign: what for, who, where, WHICH LEADS, what kind, and how much.
GATING: tuple[tuple[str, str], ...] = (
    ("goal", "What's the goal of this campaign? (e.g. fill quiet Tuesdays, win back lapsed clients)"),
    ("audience", "Who's the target audience?"),
    ("channels", "Which channels should we use? (email, instagram, facebook, sms)"),
    # LEAD SOURCE — a hard branch the operator MUST choose: go FIND new prospects on
    # the web, or comply strictly and use ONLY the operator's own leads (uploaded CSV
    # / existing database). This drives the whole orchestration mode downstream.
    ("lead_source", "Lead source: should the team SOURCE new leads from the web, or use "
                    "ONLY your uploaded CSV / existing database leads? (new / provided)"),
    ("campaign_type", "What type of campaign is this? (win-back, artist-spotlight, promo, event, birthday)"),
    ("output_count", "How many drafts/outputs should the team produce?"),
)

# Asked after the gating set is complete — they refine the run but never block it.
OPTIONAL: tuple[tuple[str, str], ...] = (
    ("action_type", "What action should the team take — outreach, posts, replies, or comments?"),
    ("deep_research", "Should the team run deep web research first? (yes/no)"),
    ("lead_count", "How many leads should we target? (0 if not a leads campaign)"),
    ("tone", "Any tone or brand-voice notes for the team to follow?"),
    ("drafts_only", "Drafts only, or stage them for your approval? (drafts / stage)"),
)

# Every field the interview is allowed to set on the plan.
INTERVIEW_FIELDS: tuple[str, ...] = tuple(f for f, _ in (*GATING, *OPTIONAL))

GATING_FIELDS: tuple[str, ...] = tuple(f for f, _ in GATING)

READY_MESSAGE = (
    "I have enough context. Say 'go ahead' or click Run to start the team — "
    "everything stays HELD for your approval."
)

# Types the supervisor renders as yes/no chips and number/text inputs on the client.
_BOOL_FIELDS = frozenset({"deep_research", "drafts_only"})
_INT_FIELDS = frozenset({"output_count", "lead_count"})
_LIST_FIELDS = frozenset({"channels"})


def field_present(plan: Any, field: str) -> bool:
    """Whether ``field`` carries a real operator-supplied value on ``plan``.

    Empty string / empty list / 0 / None all read as "not yet answered". A boolean
    field counts as present once it is explicitly True OR False (the operator made a
    choice) — i.e. only ``None`` is unanswered for a bool."""
    val = getattr(plan, field, None)
    if field in _LIST_FIELDS:
        return bool([c for c in (val or []) if str(c).strip()])
    if field in _INT_FIELDS:
        try:
            return bool(val) and int(val) > 0
        except (TypeError, ValueError):
            return False
    if field in _BOOL_FIELDS:
        return val is not None
    return bool(str(val or "").strip())


def missing_gating(plan: Any) -> list[str]:
    """The gating fields still unanswered, in ask order."""
    return [f for f in GATING_FIELDS if not field_present(plan, f)]


def is_armed(plan: Any) -> bool:
    """The arming predicate: every gating field is answered. A plan that is not armed
    can NEVER launch a run from the Agency page (the gate is enforced server-side)."""
    return not missing_gating(plan)


def next_question(plan: Any) -> dict[str, str] | None:
    """The next question to ask: the first unanswered gating field, then the first
    unanswered optional field, then ``None`` (nothing left to ask)."""
    for f, q in (*GATING, *OPTIONAL):
        if not field_present(plan, f):
            return {"field": f, "question": q}
    return None


def _has_customers(plan: Any) -> bool:
    cust = getattr(plan, "customers", None) or {}
    try:
        return bool(cust.get("rows")) or bool(cust.get("customer_ids"))
    except AttributeError:
        return False


def select_mode(plan: Any) -> tuple[str, str]:
    """The orchestration MODE the request needs, derived from the plan. ``(id, label)``.

    Pure + deterministic — the same branch the run dispatch takes (provided-leads vs
    the archetype content spine), made explicit so the UI can show WHY a given set of
    agents/tools will run."""
    action = (getattr(plan, "action_type", "") or "").strip().lower()
    lead_source = (getattr(plan, "lead_source", "") or "").strip().lower()
    drafts_only = getattr(plan, "drafts_only", None) is True

    if action in ("results", "report", "performance", "history"):
        return "performance", "Performance review — read results, not a new campaign"
    if lead_source in ("provided", "use", "own") or _has_customers(plan):
        return "personalized_outreach", "Personalized outreach to YOUR leads (CSV / database)"
    if lead_source in ("new", "source_new", "source", "web"):
        return "source_and_outreach", "Source new prospects on the web, then outreach"
    if drafts_only and not _has_customers(plan):
        return "quick_draft", "Quick draft — skip the full pipeline"
    return "content_campaign", "Content campaign — strategy, drafts, critique"


def planned_steps(plan: Any) -> list[dict[str, Any]]:
    """The pipeline steps THIS request needs, each marked selected/skipped WITH a
    reason and the tools it uses. Pure projection of the plan — the supervisor plans
    which steps to run, and the UI renders which ran and WHY. Honest: a skipped step
    says why it was skipped; it never silently disappears."""
    mode, _ = select_mode(plan)
    has_customers = _has_customers(plan)
    deep = getattr(plan, "deep_research", None)
    drafts_only = getattr(plan, "drafts_only", None) is True
    n = getattr(plan, "output_count", 0) or 0

    is_outreach = mode in ("personalized_outreach", "source_and_outreach")
    is_content = mode == "content_campaign"
    is_quick = mode == "quick_draft"
    is_perf = mode == "performance"

    def step(sid, label, selected, reason, tools=()):
        return {"id": sid, "label": label, "selected": bool(selected),
                "reason": reason, "tools": list(tools)}

    steps: list[dict[str, Any]] = [
        step("interview", "Scope the run", True,
             "Gathered goal, audience, channels, lead source and output count before launch.",
             ["interview gate"]),
    ]

    # Performance mode is a read, not a generation pipeline.
    if is_perf:
        steps.append(step("results", "Read results", True,
                          "You asked for performance — reading run history and outcomes, not generating new drafts.",
                          ["runs", "actions history"]))
        return steps

    # Lead analysis (per-lead DB history + memory) — only when there are real leads.
    steps.append(step(
        "lead_analysis", "Analyze your leads", is_outreach,
        ("Pull each lead's real history (city, past tattoos, persona, prior-campaign memories) "
         "from your customer DB." if is_outreach
         else "No customer list to analyze — this is a content campaign, not per-lead outreach."),
        ["customer DB", "lead memory"],
    ))

    # Web research — gated on the operator's deep_research choice (or web sourcing).
    web_selected = (deep is True) or mode == "source_and_outreach"
    if deep is False:
        web_reason = "You opted out of deep web research — drafting from grounded facts only."
    elif web_selected:
        web_reason = "Cited web research (real Firecrawl URLs) about each studio / the niche."
    else:
        web_reason = "Deep research not requested — skipping the web research step."
    steps.append(step("web_research", "Web research", web_selected, web_reason, ["Firecrawl"]))

    # Brand voice — ALWAYS loaded; it grounds every draft (P1).
    steps.append(step("brand_voice", "Load brand voice", True,
                      "Load your studio's brand voice so every draft sounds like you and stays on approved claims.",
                      ["brand-voice pack"]))

    # Strategy — content campaigns plan an angle; outreach/quick drafts do not.
    steps.append(step("strategy", "Set strategy", is_content,
                      ("Pick the angle and conversion goal for the campaign." if is_content
                       else "Skipped — a single personalized message, not a multi-asset campaign strategy."),
                      ["strategist cell"]))

    # Drafting — always (the actual generation).
    draft_reason = (
        f"Write {n} personalized draft(s), one per lead, in your brand voice." if is_outreach and n
        else "Write one personalized draft per lead in your brand voice." if is_outreach
        else f"Write {n} on-voice draft(s)." if (is_content and n)
        else "Rewrite / draft in your brand voice." if is_quick
        else "Write the on-voice draft(s)."
    )
    steps.append(step("draft", "Write drafts", True, draft_reason,
                      ["copywriter cell"]))

    # Independent critic — runs for content campaigns; skipped for drafts-only.
    critic_selected = is_content and not drafts_only
    steps.append(step("critic", "Independent critic", critic_selected,
                      ("An independent critic judges each asset against your brand voice and approved claims."
                       if critic_selected
                       else "Skipped — drafts-only / single message; no separate critique pass."),
                      ["critic cell"]))

    # Jury aggregate — content + outreach runs produce a HELD jury summary.
    steps.append(step("jury", "Jury aggregate", not is_quick,
                      ("Aggregate confidence across the drafts (HELD — approve-first)." if not is_quick
                       else "Skipped for a quick one-off draft."),
                      ["jury"]))

    # Review queue — ALWAYS; nothing sends without you.
    steps.append(step("review", "Stage for approval", True,
                      "Stage every draft HELD in your Review Queue — nothing is ever sent without your approval.",
                      ["review queue"]))
    return steps


def interview_state(plan: Any) -> dict[str, Any]:
    """The full gate state for one session plan — exactly what the Agency interview
    panel renders and what gates the Run button. Pure projection of the plan."""
    missing = missing_gating(plan)
    armed = not missing
    collected = {f: getattr(plan, f, None) for f in INTERVIEW_FIELDS}
    mode, mode_label = select_mode(plan)
    return {
        "armed": armed,
        "missing": missing,
        "collected": collected,
        "nextQuestion": next_question(plan),
        "readyMessage": READY_MESSAGE if armed else None,
        "gatingFields": list(GATING_FIELDS),
        # P4 dynamic selection: the steps THIS request needs + WHY (selected/skipped).
        "mode": mode,
        "modeLabel": mode_label,
        "plannedSteps": planned_steps(plan),
    }


# --------------------------------------------------------------------------- #
# Coercion — turn a raw answer (text / number / yes-no) into the typed plan value.
# --------------------------------------------------------------------------- #

_YES = {"yes", "y", "true", "1", "on", "sure", "yep", "please do", "do it"}
_NO = {"no", "n", "false", "0", "off", "nope", "skip", "don't", "dont"}

# Lead-source answers, normalized to the two canonical modes. PROVIDED = comply
# strictly with the operator's own leads (uploaded CSV / existing DB); SOURCE_NEW =
# go find new prospects on the web. Unrecognized -> "" (stays unanswered, no guess).
LEAD_SOURCE_PROVIDED = "provided"
LEAD_SOURCE_NEW = "source_new"
_LS_PROVIDED = {
    "provided", "use provided", "use my leads", "my leads", "csv", "uploaded csv",
    "database", "db", "existing", "existing db", "existing database", "uploaded",
    "list", "mine", "use csv", "use db", "use database", "use existing", "only mine",
}
_LS_NEW = {
    "new", "source_new", "source new", "source new leads", "scrape", "web", "internet",
    "find", "find new", "fresh", "rough", "new leads", "source", "scrape new",
}


def _coerce_lead_source(value: Any) -> str:
    s = str(value or "").strip().lower()
    if not s:
        return ""
    if s in _LS_PROVIDED:
        return LEAD_SOURCE_PROVIDED
    if s in _LS_NEW:
        return LEAD_SOURCE_NEW
    # substring fallback so a sentence answer still classifies, provided-biased on
    # explicit ownership words ("my", "csv", "database", "existing", "uploaded").
    if any(w in s for w in ("csv", "database", " db", "existing", "uploaded", "my lead", "provided", "only mine")):
        return LEAD_SOURCE_PROVIDED
    if any(w in s for w in ("scrape", "web", "internet", "new lead", "find", "source new", "fresh", "rough")):
        return LEAD_SOURCE_NEW
    return ""


def _coerce_bool(value: Any, *, field: str) -> bool | None:
    if isinstance(value, bool):
        return value
    s = str(value or "").strip().lower()
    if field == "drafts_only":
        # "drafts" -> drafts only (True); "stage"/"approve" -> stage for approval (False)
        if s in ("drafts", "drafts only", "draft"):
            return True
        if s in ("stage", "staged", "approval", "approve", "stage for approval"):
            return False
    if s in _YES:
        return True
    if s in _NO:
        return False
    return None


def _coerce_int(value: Any) -> int:
    if isinstance(value, bool):  # guard: bool is an int subclass
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    import re

    m = re.search(r"-?\d+", str(value or ""))
    return int(m.group()) if m else 0


def _coerce_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    # free text: split on commas / "and" / whitespace into channel tokens
    import re

    parts = re.split(r"[,;/]|\band\b", str(value or ""), flags=re.IGNORECASE)
    return [p.strip().lower() for p in parts if p.strip()]


def coerce_field(field: str, value: Any) -> Any:
    """Coerce a raw interview answer into the typed value the plan field expects.
    Unknown fields pass through as a stripped string."""
    if field == "lead_source":
        return _coerce_lead_source(value)
    if field in _BOOL_FIELDS:
        return _coerce_bool(value, field=field)
    if field in _INT_FIELDS:
        return _coerce_int(value)
    if field in _LIST_FIELDS:
        return _coerce_list(value)
    return str(value or "").strip()


def apply_fields(plan: Any, fields: dict[str, Any]) -> Any:
    """Apply a dict of ``{field: raw_value}`` interview answers to ``plan`` in place,
    coercing each to the right type and ignoring any non-interview key (so the route
    can pass the request body straight through). A bool coerced to ``None`` (an
    unrecognized yes/no) is skipped — it stays unanswered rather than guessing."""
    for key, raw in (fields or {}).items():
        if key not in INTERVIEW_FIELDS:
            continue
        coerced = coerce_field(key, raw)
        if key in _BOOL_FIELDS and coerced is None:
            continue
        setattr(plan, key, coerced)
    return plan
