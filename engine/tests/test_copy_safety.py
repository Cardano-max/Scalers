"""COPY-SAFETY regression tests — CustomerAcq-65w.13 (2026-07-02 reality audit).

THE BUGS (reproduced on the deterministic/no-credit template path, the path 100% of
drafts take while the Anthropic key is credit-out): ``_template_outreach``

  1. spliced the operator's INTERNAL campaign goal verbatim into the customer body
     ("…wanted to say hello and win back lapsed clients and convert warm leads to
     bookings.") — the recipient is literally told they are a lapsed client;
  2. presented the RECIPIENT's city as the SENDER's ("I run a small studio over in
     Brooklyn" to a Brooklyn lead; "Fellow Brooklyn studio saying hi" subject);
  3. addressed lapsed CUSTOMERS as peer studios ("reaching out from one studio to
     another") on the base angles (csv-note / their-positioning / local / generic).

Fail-safe rule under test: peer-studio (B2B) framing ONLY for a lead whose
``customer_type`` explicitly says studio/shop/b2b/partner; unknown/blank defaults to
CUSTOMER framing. Sender location comes only from an explicit ``sender_city`` (omit
when unknown) — never from the recipient's row. All deterministic + keyless.
"""

from __future__ import annotations

import pytest

from studio.customer_research import _template_outreach, build_outreach_draft

# The internal goal the operator typed — must NEVER appear in customer copy.
GOAL = "win back lapsed clients and convert warm leads to bookings"

# Phrasings that mark peer-studio (B2B) copy. Any of these reaching a customer is the bug.
B2B_MARKERS = (
    "one studio to another",
    "fellow brooklyn studio",
    "fellow austin studio",
    "studio here, saying hello",
    "kindred",
    # the peer-studio self-intro closing — a customer gets the warm studio line instead
    "i run a small studio",
)


def _customer(**over) -> dict:
    base = {
        "customer_id": "cust_t",
        "name": "Rae Torres",
        "email": "rae@example.com",
        "email_opt_in": True,
        "city": "Brooklyn",
        "persona_traits": {},
        "interests": [],
        "tattoo_history": [],
        "customer_type": "lapsed customer",
    }
    base.update(over)
    return base


def _assert_customer_safe(subject: str | None, body: str) -> None:
    low_body = body.lower()
    low_subj = (subject or "").lower()
    # Bug 1 — no internal goal text (whole or fragment) in customer copy.
    assert GOAL not in body, f"campaign goal leaked into body: {body!r}"
    assert "win back lapsed" not in low_body
    assert "convert warm leads" not in low_body
    # Bug 3 — no peer-studio framing to a customer, in body or subject.
    for marker in B2B_MARKERS:
        assert marker not in low_body, f"B2B phrasing {marker!r} in customer body: {body!r}"
        assert marker not in low_subj, f"B2B phrasing {marker!r} in customer subject: {subject!r}"


@pytest.fixture(autouse=True)
def _no_llm(monkeypatch):
    monkeypatch.setenv("SCALERS_OUTREACH_LLM", "0")  # the credit-out template path


# ── Bug 1 + 2 + 3: lapsed customer in a different city than the tenant ─────── #


def test_lapsed_customer_different_city_gets_safe_customer_copy():
    draft = build_outreach_draft(_customer(), goal=GOAL, channel="gmail")
    subject, body = draft["subject"], draft["draft"]
    _assert_customer_safe(subject, body)
    # Bug 2 — the recipient's city is never presented as the sender's.
    assert "over in Brooklyn" not in body
    assert "fellow brooklyn" not in body.lower()
    assert "brooklyn" not in (subject or "").lower()


def test_goal_never_leaks_regardless_of_angle():
    # Sweep the customer through several real-data shapes (different angles fire);
    # the internal goal text must never appear in any of them.
    variants = [
        _customer(),  # local (city)
        _customer(city=None),  # generic
        _customer(city=None, notes="interested in a small blackwork piece"),  # csv-note
        _customer(city=None, interests=["fine-line floral"]),  # shared-craft
        _customer(city=None, tattoo_history=[{"style": "blackwork"}]),  # past-work
    ]
    for facts in variants:
        draft = build_outreach_draft(facts, goal=GOAL, channel="gmail")
        assert GOAL not in draft["draft"], draft["angle_key"]
        assert "win back lapsed" not in draft["draft"].lower(), draft["angle_key"]


# ── Bug 3: thin-profile person lead (the real-CSV case) ────────────────────── #


def test_thin_profile_person_lead_gets_customer_intro_not_b2b():
    facts = _customer(
        name="Sarah Kim",
        city=None,
        customer_type="warm lead",
        notes=None,
    )
    draft = build_outreach_draft(facts, goal=GOAL, channel="gmail")
    _assert_customer_safe(draft["subject"], draft["draft"])
    # Still the honest generic angle (no fabricated personalization).
    assert draft["angle_key"] == "generic"
    assert draft["generic"] is True
    assert any(g == "personalization=generic-honest" for g in draft["grounding"])


def test_unknown_customer_type_defaults_to_customer_framing():
    # customer_type missing/blank -> consumer framing, never peer-studio (AC edge case).
    for ct in (None, "", "  "):
        draft = build_outreach_draft(
            _customer(name="Sam Ortiz", city=None, customer_type=ct),
            goal=GOAL,
            channel="gmail",
        )
        _assert_customer_safe(draft["subject"], draft["draft"])


def test_internal_crm_notes_are_never_quoted_into_the_body():
    # The CRM note is staff-written INTERNAL text ("hesitated when we talked price")
    # — same exposes-internal-wording class as the goal leak. It may ground the ANGLE
    # (operator-facing rationale) but must never be spliced into the outgoing body.
    note = "asked about a fine-line peony; hesitated when we talked price"
    for ct in ("warm lead", "studio"):  # customer AND B2B — notes are internal either way
        draft = build_outreach_draft(
            _customer(name="Dana Ruiz", city=None, customer_type=ct, notes=note),
            goal=GOAL,
            channel="gmail",
        )
        low = draft["draft"].lower()
        assert "hesitated" not in low, (ct, draft["draft"])
        assert "saw on our end" not in low, (ct, draft["draft"])
        assert note not in low, (ct, draft["draft"])


# ── Bug 2: sender city comes only from the explicit sender, never the row ──── #


def test_sender_city_never_derived_from_recipient_row():
    # Direct template call: a studio lead in Denver, sender city UNKNOWN -> the
    # location phrase is omitted entirely (never "over in Denver").
    facts = _customer(name="Bold Crow Tattoo", city="Denver", customer_type="studio")
    _, body = _template_outreach(
        facts,
        goal=GOAL,
        ch="gmail",
        angle={"key": "generic"},
    )
    assert "over in" not in body
    assert "Denver" not in body


def test_explicit_sender_city_is_used_when_provided():
    facts = _customer(name="Bold Crow Tattoo", city="Denver", customer_type="studio")
    _, body = _template_outreach(
        facts,
        goal=GOAL,
        ch="gmail",
        angle={"key": "generic"},
        sender_city="Austin",
    )
    assert "over in Austin" in body  # the SENDER's real city
    assert "over in Denver" not in body  # never the recipient's


# ── Peer-studio framing survives ONLY for explicit studio leads ─────────────── #


def test_explicit_studio_lead_may_keep_peer_studio_framing():
    facts = _customer(name="Bold Crow Tattoo", city=None, customer_type="studio")
    _, body = _template_outreach(facts, goal=GOAL, ch="gmail", angle={"key": "generic"})
    # B2B intro is still legitimate studio-to-studio copy…
    assert "one studio to another" in body.lower()
    # …but even for a studio the internal goal never leaks.
    assert GOAL not in body


def test_studio_lead_body_never_contains_goal_text_either():
    facts = _customer(
        name="La Emme Tattoo Studio",
        city=None,
        customer_type="studio",
        notes="official email for studio inquiries",
    )
    draft = build_outreach_draft(facts, goal=GOAL, channel="gmail")
    assert GOAL not in draft["draft"]
    assert "win back lapsed" not in draft["draft"].lower()


# ── Adversarial-verification findings (ultracode pass) ─────────────────────── #


def test_shared_craft_never_claims_sender_affinity_from_recipient_data():
    # "we share a soft spot for X… We spend a lot of our time there too" asserted the
    # SENDER's affinity/presence from recipient-row data; with a place-bearing interest
    # it becomes an implied false sender-location claim (same class as bug 2).
    draft = build_outreach_draft(
        _customer(
            name="Ana Reyes",
            city=None,
            customer_type="warm lead",
            interests=["the Denver tattoo scene"],
        ),
        goal=GOAL,
        channel="gmail",
    )
    low = draft["draft"].lower()
    assert draft["angle_key"] == "shared-craft"
    assert "we share a soft spot" not in low
    assert "we spend a lot of our time there" not in low
    # The interest still grounds the copy — recipient-centered, no sender claim.
    assert "denver tattoo scene" in low


def test_same_city_match_renders_the_senders_own_city_string():
    # A messy recipient row ("  bRoOkLyN ") must never be rendered as OUR identity —
    # when the local claim is genuinely true, the SENDER's canonical string is used.
    facts = _customer(name="Bold Crow Tattoo", city="  bRoOkLyN ", customer_type="studio")
    subject, body = _template_outreach(
        facts, goal=GOAL, ch="gmail", angle={"key": "local"}, sender_city="Brooklyn"
    )
    assert "bRoOkLyN" not in body and "bRoOkLyN" not in (subject or "")
    assert "fellow Brooklyn studio" in body
    assert (subject or "").startswith("Fellow Brooklyn studio")


def test_is_studio_lead_requires_exact_type_not_substring():
    # 'studio walk-in' / 'business owner' / 'partner referral' are CONSUMER rows whose
    # type merely CONTAINS a B2B word — substring matching failed open toward the
    # embarrassing direction. Exact-token values only.
    for consumer_ct in ("studio walk-in", "business owner", "partner referral"):
        draft = build_outreach_draft(
            _customer(name="Sam Ortiz", city=None, customer_type=consumer_ct),
            goal=GOAL,
            channel="gmail",
        )
        _assert_customer_safe(draft["subject"], draft["draft"])
    # Real studio values still get B2B framing.
    for studio_ct in ("studio", "Tattoo Shop"):
        _, body = _template_outreach(
            _customer(name="Bold Crow Tattoo", city=None, customer_type=studio_ct),
            goal=GOAL,
            ch="gmail",
            angle={"key": "generic"},
        )
        assert "one studio to another" in body.lower(), studio_ct


# ── ju1.3: anti-fake-personalization on the deterministic (history-less) path ──


def test_historyless_customer_draft_makes_no_personalization_claim() -> None:
    # A skindesign-shaped lead: name + contact only, no interests / history / social.
    # The deterministic draft must ground-honest (generic) and trip ZERO personalization
    # claims — the anti-theater guarantee, proven end-to-end through build_outreach_draft.
    from cells.personalization_guard import personalization_violations

    lead = _customer(
        name="Sam Rivera", email="sam@example.com", city="", interests=[],
        tattoo_history=[], persona_traits={}, customer_type="",
    )
    draft = build_outreach_draft(lead, goal=GOAL, plan_channels=["email"])
    text = f"{draft.get('subject') or ''}\n{draft.get('draft') or ''}"
    assert personalization_violations(text, lead) == []
    # And it is still customer-safe (no goal leak / no B2B framing).
    _assert_customer_safe(draft.get("subject"), draft.get("draft") or "")


def test_historyless_sms_shaped_draft_has_no_history_or_social_claim() -> None:
    from cells.personalization_guard import personalization_violations

    lead = _customer(
        name="Jo", email="", phone="+15551230000", city="", interests=[],
        tattoo_history=[], ig_handle="", customer_type="",
    )
    draft = build_outreach_draft(lead, goal=GOAL, plan_channels=["sms"])
    text = f"{draft.get('subject') or ''}\n{draft.get('draft') or ''}"
    # No fabricated "your last tattoo" / "I saw your Instagram" for a contact-only lead.
    assert personalization_violations(text, lead) == []
