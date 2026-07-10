"""OBJECTION INTELLIGENCE — the three real-thread labels + their honest angles.

Pins the taxonomy extension learned from the client's REAL SMS threads (tenant
skindesign): ``trust_concern`` (deposit double-reschedule -> refund dispute -> "no
longer confident"), ``blocked_by_prereq`` (cover-up blocked until laser removal), and
``went_quiet_mid_booking`` (mid-booking silence at the send-photos step). Each test
feeds a SYNTHETIC thread echoing the real case and asserts the label AND that the
evidence quotes the verbatim trigger line — classification stays EVIDENCE-ONLY, rules
first, zero tokens. Plus: the angle each label maps to (never a hard sell / discount
pitch), and the sms_opt_in=False channel guard ("SMS suppressed — email only").

All deterministic (``use_llm=False`` / ``SCALERS_OUTREACH_LLM=0``) — no model, no DB.
"""

from __future__ import annotations

from studio.customer_research import (
    _build_email_prompt,
    _offer_prompt_block,
    build_outreach_draft,
)
from studio.psych_profile import INFERRED, STATED, analyze_customer
from studio.reason_history import (
    OBJECTION_BLOCKED_PREREQ,
    OBJECTION_TRUST_CONCERN,
    OBJECTION_WENT_QUIET,
    extract_signals,
    parse_conversation_text,
)

# Synthetic echo of the Amanda thread: deposit paid -> both dates rescheduled by the
# shop -> refund request -> bank-dispute + review language -> "no longer confident".
_TRUST_THREAD = (
    "Customer: Hi there! Wanted to follow up as I am ready to move forward. / "
    "Customer: I can provide the deposit to secure dates. / "
    "Studio: Just sent the invoice over! / Customer: Okay paid. / "
    "Studio: We were just made aware of a scheduling conflict for your appt, we will need to reschedule. / "
    "Customer: I'll take the following Friday then. / "
    "Studio: There's a scheduling conflict on that date too, could you do the week after? / "
    "Customer: Both of those dates were rescheduled due to a scheduling conflict. "
    "At this juncture, I would like to request a refund of my deposit. / "
    "Studio: We do have a non-refundable deposit policy. / "
    "Customer: I can go the route of writing a review and going through my bank to "
    "dispute the charge but would rather not. / "
    "Studio: If we were able to get you in asap would this give you any relief? / "
    "Customer: No, thank you. I am no longer confident in your shop."
)

# Synthetic echo of the Todd thread: actively booking, asked for photos of the area,
# never replied; the shop nudged twice more into silence.
_QUIET_THREAD = (
    "Studio: Hey Todd, this is the booking manager. What days work best, and what "
    "design did you have in mind? / "
    "Customer: Sorry about the delay, been out of town. I'd like to see about possible "
    "weekends or evenings. Looking for lettering in tall thin letters across my neck. / "
    "Studio: Would you mind sending over a photo of the font style you'd prefer? / "
    "Customer: I kinda have a small beard at the moment so I need to have that trimmed "
    "before coming in. / "
    "Studio: No worries, please send over photos of your neck area and we can proceed "
    "with locking you in. / "
    "Studio: Rates go back up next week, want sample monthly payments? / "
    "Studio: Just wanted to touch base to see if you had a chance to check out the "
    "previous message?"
)

# Synthetic echo of the Lauren thread: ready for a cover-up, but the artists require
# laser removal sessions first (the prerequisite is stated by the STUDIO turn).
_PREREQ_THREAD = (
    "Studio: For a limited time, get a special rate on new bookings with our selected "
    "artists! / "
    "Customer: Hey I'm ready for a cover up, want to cover the name on my arm. / "
    "Customer: Rose or sunflower, it doesn't have to be exact. / "
    "Studio: Our artists recommended going through a couple of laser removal sessions "
    "prior to covering up your tattoo because it's quite large and dark. Would you be "
    "open to starting that process first?"
)


def _facts(**kw):
    base = {"customer_id": "c1", "name": "Jamie", "email": "j@x.com", "email_opt_in": True,
            "persona_traits": {}, "interests": [], "tattoo_history": []}
    base.update(kw)
    return base


# --------------------------------------------------------------------------- #
# Classifier: label + verbatim trigger-line evidence (rules only, zero tokens).
# --------------------------------------------------------------------------- #
def test_trust_concern_from_refund_dispute_thread():
    sig = extract_signals(parse_conversation_text(_TRUST_THREAD))
    # trust_concern is PRIMARY despite the earlier incidental "deposit" mention (which
    # still surfaces as a secondary payment read, never the lead's headline).
    assert sig.primary_objection is not None
    assert sig.primary_objection.value == OBJECTION_TRUST_CONCERN
    assert "request a refund of my deposit" in sig.primary_objection.evidence
    # The customer's own words -> a STATED psych read, evidence verbatim.
    prof = analyze_customer(_facts(), parse_conversation_text(_TRUST_THREAD), use_llm=False)
    assert prof.primary_objection.value == OBJECTION_TRUST_CONCERN
    assert prof.primary_objection.signal == STATED
    assert "refund of my deposit" in prof.primary_objection.evidence
    # A trust breach reads trust low, grounded on the same real span.
    assert prof.trust_level.value == "low"


def test_blocked_by_prereq_from_laser_removal_thread():
    sig = extract_signals(parse_conversation_text(_PREREQ_THREAD))
    assert sig.primary_objection is not None
    assert sig.primary_objection.value == OBJECTION_BLOCKED_PREREQ
    assert "laser removal sessions" in sig.primary_objection.evidence
    # The quote is a STUDIO turn -> honestly INFERRED, never asserted as their words.
    assert sig.primary_objection.source == "studio"
    prof = analyze_customer(_facts(), parse_conversation_text(_PREREQ_THREAD), use_llm=False)
    assert prof.primary_objection.value == OBJECTION_BLOCKED_PREREQ
    assert prof.primary_objection.signal == INFERRED
    assert "laser removal" in prof.primary_objection.evidence


def test_went_quiet_mid_booking_quotes_the_exact_stalled_step():
    sig = extract_signals(parse_conversation_text(_QUIET_THREAD))
    assert sig.primary_objection is not None
    assert sig.primary_objection.value == OBJECTION_WENT_QUIET
    # Evidence = the studio's unanswered ask: the concrete step they stopped at.
    assert "send over photos of your neck area" in sig.primary_objection.evidence
    prof = analyze_customer(_facts(), parse_conversation_text(_QUIET_THREAD), use_llm=False)
    assert prof.primary_objection.value == OBJECTION_WENT_QUIET
    assert prof.primary_objection.signal == INFERRED
    assert "send over photos" in prof.primary_objection.evidence


def test_opt_out_is_not_went_quiet():
    # A final "Stop" is a STATED choice — never relabelled as silence.
    sig = extract_signals(parse_conversation_text(
        "Customer: I want to book a session for a rose piece. / Studio: Great! / "
        "Customer: Stop / "
        "Studio: Just checking in, still interested? / "
        "Studio: Last chance, please reply!"
    ))
    assert OBJECTION_WENT_QUIET not in sig.objection_types()


def test_insufficient_signal_behavior_is_unchanged():
    # A live thread with no objection stays honestly "none-found" — a single unanswered
    # closing message is a normal thread end, not "ghosting".
    conv = parse_conversation_text(
        "Studio: Hi! / Customer: Hey I'm ready for a cover up on my arm. / "
        "Studio: Great, what were you thinking?"
    )
    prof = analyze_customer(_facts(), conv, use_llm=False)
    assert prof.primary_objection.value == "none-found"
    # No conversation at all -> insufficient-signal, never an invented label.
    blank = analyze_customer(_facts(), conversation=None, use_llm=False)
    assert blank.primary_objection.value == ""


# --------------------------------------------------------------------------- #
# Angle map: each new label -> its honest angle (no hard sell, no discount pitch).
# --------------------------------------------------------------------------- #
def test_trust_concern_angle_never_hard_sells(monkeypatch):
    monkeypatch.setenv("SCALERS_OUTREACH_LLM", "0")
    prof = analyze_customer(_facts(), parse_conversation_text(_TRUST_THREAD), use_llm=False)
    draft = build_outreach_draft(_facts(), goal="win back", channel="gmail",
                                 profile=prof, offer=None)
    assert draft["angle_key"] == "rebuild-trust"
    assert "objection=trust_concern" in draft["grounding"]
    body = draft["draft"].lower()
    # Direct-artist commitment + no-reschedule guarantee + manager point of contact.
    assert "directly" in body and "reschedul" in body and "manager" in body
    # NEVER a hard sell: no promo/discount/urgency language.
    for banned in ("%", "discount", "promo", "offer", "last chance"):
        assert banned not in body
    # The acknowledgment never restates the painful details from the thread.
    assert "refund" not in body and "dispute" not in body


def test_trust_concern_withholds_even_a_real_offer():
    # The prompt-side guard: a substantiated offer is still withheld for trust repair,
    # and the prerequisite label is explicitly not a discount pitch.
    from studio.offers import _SEED_OFFERS, parse_offers_doc

    offer = parse_offers_doc(_SEED_OFFERS)[0]
    block = "\n".join(_offer_prompt_block(offer, "trust_concern"))
    assert "TRUST-REPAIR GUARD" in block and offer.code not in block
    block = "\n".join(_offer_prompt_block(offer, "blocked_by_prereq"))
    assert "NOT a discount pitch" in block and offer.code not in block


def test_blocked_by_prereq_angle_is_help_not_discount(monkeypatch):
    monkeypatch.setenv("SCALERS_OUTREACH_LLM", "0")
    prof = analyze_customer(_facts(), parse_conversation_text(_PREREQ_THREAD), use_llm=False)
    draft = build_outreach_draft(_facts(), goal="reactivate", channel="gmail",
                                 profile=prof, offer=None)
    assert draft["angle_key"] == "prereq-help"
    assert "objection=blocked_by_prereq" in draft["grounding"]
    # The angle rationale stands on the REAL prerequisite quote from the thread.
    assert "laser removal" in draft["why_different"]
    body = draft["draft"].lower()
    assert "next step" in body
    for banned in ("%", "discount", "promo"):
        assert banned not in body


def test_went_quiet_angle_resumes_at_the_exact_step(monkeypatch):
    monkeypatch.setenv("SCALERS_OUTREACH_LLM", "0")
    prof = analyze_customer(_facts(), parse_conversation_text(_QUIET_THREAD), use_llm=False)
    draft = build_outreach_draft(_facts(), goal="follow up", channel="gmail",
                                 profile=prof, offer=None)
    assert draft["angle_key"] == "resume-booking"
    assert "objection=went_quiet_mid_booking" in draft["grounding"]
    # The rationale references the exact step they stopped at (from evidence).
    assert "send over photos" in draft["why_different"]
    body = draft["draft"].lower()
    assert "where we left off" in body
    assert "no pressure" in body


def test_new_labels_route_to_their_plays():
    from types import SimpleNamespace as NS

    from studio.dossier import build_dossier
    from studio.skill_select import select_skill

    def _sel(objection):
        profile = NS(primary_objection=NS(value=objection, signal="stated",
                                          evidence="they said so"),
                     umbrella_category=NS(value="", signal=""),
                     had_conversation=True, where_customer_sits="considering",
                     source="deterministic")
        dossier = build_dossier(
            _facts(), profile=profile,
            angle={"label": "x", "key": "rebuild-trust", "generic": False,
                   "inferred": False},
            channel="gmail", cta_kind="reply-based",
        )
        return select_skill(dossier)

    assert _sel("trust_concern").skill_id == "trust-repair"
    assert _sel("blocked_by_prereq").skill_id == "prereq-help"
    assert _sel("went_quiet_mid_booking").skill_id == "resume-booking"


# --------------------------------------------------------------------------- #
# Opt-out respect: sms_opt_in=False suppresses SMS and notes the brief.
# --------------------------------------------------------------------------- #
def test_sms_opt_out_suppresses_sms_and_notes_the_brief(monkeypatch):
    monkeypatch.setenv("SCALERS_OUTREACH_LLM", "0")
    lead = _facts(phone="+17025550100", sms_opt_in=False)
    # Even an explicit SMS request is downgraded — withheld consent is never overridden.
    draft = build_outreach_draft(lead, goal="follow up", channel="sms")
    assert draft["channel"] != "sms"
    assert "channel_guard=SMS suppressed — email only" in draft["grounding"]
    # The default path carries the same note.
    draft = build_outreach_draft(lead, goal="follow up")
    assert draft["channel"] == "gmail"
    assert "channel_guard=SMS suppressed — email only" in draft["grounding"]
    # And the copywriter brief tells the strategy never to propose texting.
    prompt = _build_email_prompt(
        lead, goal="follow up", research=[],
        angle={"key": "generic", "label": "x", "basis": "y",
               "inferred": False, "generic": True},
    )
    assert "SMS suppressed — email only" in prompt


def test_absent_sms_flag_is_not_an_opt_out(monkeypatch):
    monkeypatch.setenv("SCALERS_OUTREACH_LLM", "0")
    draft = build_outreach_draft(_facts(), goal="follow up", channel="gmail")
    assert not any(g.startswith("channel_guard=") for g in draft["grounding"])
