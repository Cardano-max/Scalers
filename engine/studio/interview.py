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
# campaign: what for, who, where, WHICH LEADS, what kind, how much, and the ask.
#
# Phrasing rule: every question is worded the way a friendly senior agency exec would
# put it to a NON-TECHNICAL client — plain language, a concrete for-instance, no
# jargon ("lead source", "output count", "channels" never appear bare). The field
# NAMES stay stable (downstream run logic reads them); only the wording is layman.
GATING: tuple[tuple[str, str], ...] = (
    ("goal", "First — what are you hoping this campaign actually does for you? "
             "(for example: fill up your quiet Tuesdays, win back clients you haven't "
             "seen in a while, or get the word out about a new flash sheet)"),
    ("audience", "Who exactly are we trying to reach? "
                 "(for example: your past clients, people who follow you on Instagram, "
                 "folks in your neighbourhood, or fans of a particular style)"),
    ("channels", "How should we reach them — email, Instagram, Facebook, text message, "
                 "or a mix of those?"),
    # LEAD SOURCE — a hard branch the operator MUST choose: go FIND new prospects on
    # the web, or comply strictly and use ONLY the operator's own leads (uploaded CSV
    # / existing database). This drives the whole orchestration mode downstream.
    ("lead_source", "Should I reach out to ONLY the people on the list you've uploaded "
                    "(your own clients / CSV), or should I also go and find brand-new "
                    "prospects for you online?"),
    ("campaign_type", "What kind of campaign is this — winning back old clients, "
                      "spotlighting one of your artists, a promo or sale, an event, or a "
                      "birthday note?"),
    ("output_count", "Roughly how many people should this go out to — how many messages "
                     "should the team create?"),
    # OFFER / CTA — what we actually want the reader to DO. A campaign with no ask is
    # half a campaign, so this gates the run alongside the rest.
    ("offer", "What's the offer or the ask? "
              "(for example: a booking link, a discount code, or just 'reply to book')"),
)

# Asked after the gating set is complete — they refine the run but never block it.
# Each has a sensible default so the operator can say "use your judgment" and move on.
OPTIONAL: tuple[tuple[str, str], ...] = (
    ("per_lead", "Should each person get their own personalized message, or do you want "
                 "one shared message that goes to everyone? (personalized / shared)"),
    ("personalize", "Want me to use each person's history and social profiles to tailor "
                    "their message? (yes/no)"),
    ("deep_research", "Should I dig into each lead with deeper web research first before "
                      "writing? (yes/no)"),
    ("tone", "Any particular tone you'd like — warm, playful, professional? "
             "(or leave it to your brand voice)"),
    ("action_type", "And what should the team actually produce — outreach messages, "
                    "social posts, replies, or comments?"),
    ("lead_count", "How many leads should we target? (put 0 if this isn't a leads "
                   "campaign)"),
    ("drafts_only", "Should the team just write drafts, or stage them in your Review "
                    "Queue for approval? (drafts / stage)"),
    # P1 (tattoo pivot) — refine WHO and HOW. All optional so they never block the run.
    ("target_category", "Which kind of customer are we focusing on — new enquiries, a "
                        "specific artist's leads, people who booked but haven't paid, "
                        "regulars, or folks we haven't seen in a while? (or 'all')"),
    ("scope", "How wide should this go — one artist, one shop, or the whole studio?"),
    ("use_conversation_history", "Should the team read each person's past messages with "
                                 "you to tailor the outreach? (yes/no)"),
    ("attach_artwork", "Want us to match and attach the right artist's artwork to each "
                       "message where it fits? (yes/no)"),
    # --- P1-B: executive discovery questions (typed CTA, segmentation, why-no-convert,
    #     prior contact). All optional so they never block the run, but the interview
    #     DOES ask them and the answers flow into the plan summary + the run. --------- #
    ("offer_type", "What's the main thing you want them to do? "
                   "(book an appointment, a free consult, grab a flash design, a "
                   "discount, a touch-up, or spotlight one of your artists)"),
    ("segment", "Where are these folks in their journey with you — brand-new/cold, warm "
                "(they've shown interest), past clients, or regulars?"),
    ("no_convert_reason", "Why do you think they haven't booked yet? "
                          "(for example: price felt steep, bad timing, still deciding, "
                          "or you're not sure)"),
    ("prior_contact", "Have you spoken with these people before — and if so, roughly what "
                      "was said? (or 'no prior contact')"),
    # --- P1-D: enrich the confirmable spec — brand voice, research depth, what's OK to
    #     personalize on, what NOT to say, and what success looks like. -------------- #
    ("brand_voice", "How should these sound — any brand-voice notes? "
                    "(for example: warm and plain-spoken, a bit playful, never salesy) "
                    "— or leave it to your usual voice"),
    ("research_depth", "How much homework should the team do before writing — a light "
                       "look, a standard pass, or deep research on each person? "
                       "(light / standard / deep)"),
    ("personalization_rules", "Any rules for making it personal — what's fair game to "
                              "mention, and what should stay off-limits?"),
    ("do_not_use", "Anything we should absolutely NOT say or mention? "
                   "(for example: no discounts, don't mention a specific artist, no emojis)"),
    ("success_criteria", "What would make this campaign a win for you? "
                         "(for example: 5 bookings, replies from regulars, filled Tuesdays)"),
)

# Every field the interview is allowed to set on the plan.
INTERVIEW_FIELDS: tuple[str, ...] = tuple(f for f, _ in (*GATING, *OPTIONAL))

GATING_FIELDS: tuple[str, ...] = tuple(f for f, _ in GATING)

READY_MESSAGE = (
    "Great — I have everything I need. Here's the plan below. Have a quick look, and "
    "when it's right, say 'go ahead' (or click Run) and the team gets started. "
    "Nothing is sent — every message is held in your Review Queue for your approval."
)

# Types the supervisor renders as yes/no chips and number/text inputs on the client.
# ``per_lead`` is a two-way choice (personalized-per-lead vs one shared) coerced like a
# bool: True = one personalized message per lead, False = one shared message.
_BOOL_FIELDS = frozenset({
    "deep_research", "drafts_only", "personalize", "per_lead",
    # P1 tattoo-pivot yes/no refinements (optional; never gate).
    "use_conversation_history", "attach_artwork",
})
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


def real_lead_count(plan: Any) -> int:
    """The REAL number of leads the operator uploaded — the row count the upload route
    attached to ``plan.customers`` (an int, the parsed CSV data-row count), falling back
    to the count of ingested ``customer_ids``. 0 when no list is attached. This is the
    load-bearing number the plan summary must NEVER fabricate: it is read ONLY from the
    uploaded list, never from the operator-stated ``lead_count``."""
    cust = getattr(plan, "customers", None) or {}
    try:
        rows = cust.get("rows")
    except AttributeError:
        return 0
    if isinstance(rows, bool):  # guard: bool is an int subclass
        rows = None
    if isinstance(rows, int) and rows > 0:
        return rows
    if isinstance(rows, (list, tuple)) and rows:
        return len(rows)
    ids = cust.get("customer_ids")
    if isinstance(ids, (list, tuple)) and ids:
        return len(ids)
    return 0


def plan_summary(plan: Any) -> dict[str, Any] | None:
    """The senior-exec PLAN SUMMARY shown before the run, the way an agency lead would
    read the brief back to a client and wait for a go-ahead. ``None`` until the gate is
    armed (no summary for a half-answered brief).

    HONESTY: every number is REAL — the target line uses the actual uploaded lead count
    (:func:`real_lead_count`), the create line uses the real ``output_count``, and the
    channels come from the real ``channels`` list. Never invents a lead count or a
    channel. The Review-Queue line is a fixed truth: the studio ALWAYS holds — nothing
    is ever sent without the operator, so that reassurance is stated as fact."""
    if missing_gating(plan):
        return None

    mode, _ = select_mode(plan)
    provided = mode in ("personalized_outreach", "source_and_outreach") and \
        (getattr(plan, "lead_source", "") or "").strip().lower() != "source_new"
    source_new = (getattr(plan, "lead_source", "") or "").strip().lower() == "source_new"
    n_leads = real_lead_count(plan)
    n_out = int(getattr(plan, "output_count", 0) or 0)
    channels = [str(c).strip() for c in (getattr(plan, "channels", None) or []) if str(c).strip()]
    per_lead = getattr(plan, "per_lead", None)
    personalize = getattr(plan, "personalize", None)
    deep = getattr(plan, "deep_research", None)
    tone = (getattr(plan, "tone", "") or "").strip()
    offer = (getattr(plan, "offer", "") or "").strip()
    goal = (getattr(plan, "goal", "") or "").strip()
    # P1-B executive-discovery answers (all optional; shown only when answered).
    offer_type = (getattr(plan, "offer_type", "") or "").strip()
    segment = (getattr(plan, "segment", "") or "").strip()
    no_convert_reason = (getattr(plan, "no_convert_reason", "") or "").strip()
    prior_contact = (getattr(plan, "prior_contact", "") or "").strip()
    # P1-D enriched-spec answers (all optional; shown only when answered).
    brand_voice = (getattr(plan, "brand_voice", "") or "").strip()
    research_depth = (getattr(plan, "research_depth", "") or "").strip()
    personalization_rules = (getattr(plan, "personalization_rules", "") or "").strip()
    do_not_use = (getattr(plan, "do_not_use", "") or "").strip()
    success_criteria = (getattr(plan, "success_criteria", "") or "").strip()

    _OFFER_TYPE_LABELS = {
        "booking": "book an appointment",
        "consult": "book a free consult",
        "flash": "grab a flash design",
        "discount": "use a discount",
        "touch-up": "book a touch-up",
        "artist-spotlight": "check out an artist spotlight",
    }

    lines: list[dict[str, str]] = []

    # Target — REAL lead count when we have an uploaded list.
    if source_new:
        target = "brand-new prospects I'll find for you online"
    elif n_leads:
        target = f"only your uploaded list ({n_leads} lead{'s' if n_leads != 1 else ''})"
    else:
        target = "only your uploaded leads"
    lines.append({"label": "Target", "value": target})

    # Create — REAL output count + per-lead vs shared (default: one personalized per lead).
    ch_word = channels[0] if len(channels) == 1 else "message"
    noun = ch_word if ch_word in ("email", "sms", "post") else "message"
    if per_lead is False:
        create = (f"{n_out} {noun}{'s' if n_out != 1 else ''} — one shared message"
                  if n_out else f"one shared {noun}")
    elif provided:
        create = (f"{n_out} personalized {noun}{'s' if n_out != 1 else ''}, one per lead"
                  if n_out else f"personalized {noun}s, one per lead")
    else:
        create = (f"{n_out} on-brand {noun}{'s' if n_out != 1 else ''}"
                  if n_out else f"on-brand {noun}s")
    lines.append({"label": "Create", "value": create})

    # Using — brand voice + (real) personalization + (real) research switches.
    using_bits = ["your brand voice"]
    if personalize is not False and provided:
        using_bits.append("each lead's history + profile")
    if deep is True:
        using_bits.append("deep web research first")
    lines.append({"label": "Using", "value": ", ".join(using_bits)})

    # P1-B: which lifecycle segment we're addressing (cold / warm / past / recurring).
    if segment:
        lines.append({"label": "Segment", "value": segment})

    if channels:
        lines.append({"label": "Channels", "value": ", ".join(channels)})

    # P1-B: the ask — prefer the typed CTA (offer_type) and add the free-text detail.
    if offer_type or offer:
        ask_bits: list[str] = []
        if offer_type:
            ask_bits.append(_OFFER_TYPE_LABELS.get(offer_type, offer_type))
        if offer and offer.lower() not in " ".join(ask_bits).lower():
            ask_bits.append(offer)
        lines.append({"label": "The ask", "value": " — ".join(ask_bits)})

    # P1-B: the operator's read on WHY these leads haven't booked, and prior contact.
    if no_convert_reason:
        lines.append({"label": "Why they haven't booked", "value": no_convert_reason})
    if prior_contact:
        lines.append({"label": "Prior contact", "value": prior_contact})

    # P1-D: brand voice, research depth, personalization rules, do-not-use, success.
    if brand_voice:
        lines.append({"label": "Brand voice", "value": brand_voice})
    elif tone:
        # Fall back to the lighter 'tone' answer when no explicit brand-voice note given.
        lines.append({"label": "Tone", "value": tone})
    if research_depth:
        lines.append({"label": "Research depth", "value": research_depth})
    if personalization_rules:
        lines.append({"label": "Personalization rules", "value": personalization_rules})
    if do_not_use:
        lines.append({"label": "Do NOT use", "value": do_not_use})
    if success_criteria:
        lines.append({"label": "Success looks like", "value": success_criteria})
    if tone and brand_voice:
        # keep the tone visible too if BOTH were answered (they refine each other)
        lines.append({"label": "Tone", "value": tone})

    # Fixed truth: the studio always holds. Nothing sends without the operator.
    lines.append({
        "label": "Approval",
        "value": "everything stays in your Review Queue for your approval first — "
                 "nothing is sent without you",
    })

    return {
        "title": "Here's the plan:",
        "goal": goal,
        "lines": lines,
        "leadCount": n_leads,
        "channels": channels,
        "confirm": "Say “go ahead” to run, or change any answer above.",
    }


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
        # The senior-exec plan summary the operator approves before the go-ahead.
        "planSummary": plan_summary(plan),
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


# --- P1-B typed offer/CTA menu -------------------------------------------------- #
# Canonical CTA -> the synonyms an operator might say. Coercion maps to the canonical
# value; an unrecognized answer is kept as the operator's own stripped text (honest —
# it's what they said — and it means the interview does not loop re-asking).
OFFER_TYPES: tuple[str, ...] = (
    "booking", "consult", "flash", "discount", "touch-up", "artist-spotlight",
)
_OFFER_TYPE_SYNONYMS: dict[str, str] = {
    "booking": "booking", "book": "booking", "appointment": "booking",
    "appointments": "booking", "book an appointment": "booking", "session": "booking",
    "consult": "consult", "consultation": "consult", "free consult": "consult",
    "consult call": "consult",
    "flash": "flash", "flash design": "flash", "flash sheet": "flash",
    "flash tattoo": "flash",
    "discount": "discount", "promo": "discount", "sale": "discount", "deal": "discount",
    "coupon": "discount", "offer code": "discount",
    "touch-up": "touch-up", "touch up": "touch-up", "touchup": "touch-up",
    "artist-spotlight": "artist-spotlight", "artist spotlight": "artist-spotlight",
    "spotlight": "artist-spotlight", "feature an artist": "artist-spotlight",
}


def _coerce_offer_type(value: Any) -> str:
    s = str(value or "").strip().lower()
    if not s:
        return ""
    if s in _OFFER_TYPE_SYNONYMS:
        return _OFFER_TYPE_SYNONYMS[s]
    for phrase, canon in _OFFER_TYPE_SYNONYMS.items():
        if phrase in s:
            return canon
    return str(value or "").strip()  # keep the operator's own words (no guess, no loop)


# --- P1-B lifecycle segment (cold / warm / past / recurring) --------------------- #
SEGMENTS: tuple[str, ...] = ("cold", "warm", "past", "recurring")
_SEGMENT_SYNONYMS: dict[str, str] = {
    "cold": "cold", "new": "cold", "brand new": "cold", "brand-new": "cold",
    "prospect": "cold", "prospects": "cold", "never booked": "cold",
    "warm": "warm", "hot": "warm", "interested": "warm", "inquired": "warm",
    "enquired": "warm", "engaged": "warm", "lead": "warm", "leads": "warm",
    "past": "past", "past client": "past", "past clients": "past", "lapsed": "past",
    "former": "past", "old client": "past", "old clients": "past", "inactive": "past",
    "recurring": "recurring", "regular": "recurring", "regulars": "recurring",
    "repeat": "recurring", "loyal": "recurring", "returning": "recurring",
}


def _coerce_segment(value: Any) -> str:
    s = str(value or "").strip().lower()
    if not s:
        return ""
    if s in _SEGMENT_SYNONYMS:
        return _SEGMENT_SYNONYMS[s]
    for phrase, canon in _SEGMENT_SYNONYMS.items():
        if phrase in s:
            return canon
    return str(value or "").strip()


# --- P1-D research depth (light / standard / deep) ------------------------------- #
RESEARCH_DEPTHS: tuple[str, ...] = ("light", "standard", "deep")
_RESEARCH_DEPTH_SYNONYMS: dict[str, str] = {
    "light": "light", "quick": "light", "basic": "light", "shallow": "light",
    "minimal": "light", "a light look": "light",
    "standard": "standard", "normal": "standard", "medium": "standard",
    "default": "standard", "a standard pass": "standard",
    "deep": "deep", "deep research": "deep", "thorough": "deep", "heavy": "deep",
    "detailed": "deep", "full": "deep",
}


def _coerce_research_depth(value: Any) -> str:
    s = str(value or "").strip().lower()
    if not s:
        return ""
    if s in _RESEARCH_DEPTH_SYNONYMS:
        return _RESEARCH_DEPTH_SYNONYMS[s]
    for phrase, canon in _RESEARCH_DEPTH_SYNONYMS.items():
        if phrase in s:
            return canon
    return str(value or "").strip()


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
    if field == "per_lead":
        # "personalized"/"per lead"/"each" -> one message per lead (True);
        # "shared"/"one"/"same"/"blast"/"everyone" -> one shared message (False)
        if s in ("personalized", "personalised", "personalize", "per lead", "per-lead",
                 "each", "individual", "one per lead", "one each"):
            return True
        if s in ("shared", "one", "same", "blast", "one shared", "everyone",
                 "one message", "broadcast", "single"):
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
    if field == "offer_type":
        return _coerce_offer_type(value)
    if field == "segment":
        return _coerce_segment(value)
    if field == "research_depth":
        return _coerce_research_depth(value)
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
