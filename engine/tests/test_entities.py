"""Canonical entity model — pure/offline tests.

Proves the real-backed entities build from grounded facts (never defaulting to a fake)
and the not-connected entities (Artist/Shop/Asset) raise ``NotConfiguredError`` rather
than fabricating — mirroring ``studio/adapters/``.
"""

from __future__ import annotations

import pytest

from studio import offers as offers_mod
from studio.adapters import NotConfiguredError
from studio.entities import (
    Artist,
    Asset,
    Campaign,
    Consent,
    CRMRecord,
    Lead,
    Offer,
    SendReceipt,
    Shop,
)

_FACTS = {
    "customer_id": "cust_fd6337c6058947d7",
    "name": "Sarah Kim",
    "city": "Seattle",
    "interests": ["fine-line", "floral"],
    "tattoo_history": [{"piece": "rose"}],
    "persona_traits": {"lifecycle_stage": "lapsed", "win_back_candidate": True},
    "artist": None,
}


def test_lead_and_crm_from_real_facts() -> None:
    lead = Lead.from_facts(_FACTS)
    assert lead.customer_id == "cust_fd6337c6058947d7"
    assert lead.name == "Sarah Kim" and lead.city == "Seattle"
    assert lead.interests == ["fine-line", "floral"]
    assert lead.lifecycle_stage == "lapsed" and lead.win_back_candidate is True

    crm = CRMRecord.from_facts(_FACTS)
    assert crm.past_tattoos == 1 and crm.lifecycle_stage == "lapsed"


def test_offer_and_send_receipt_wrap_real_rows() -> None:
    real = offers_mod.Offer(code="FLOWER15", discount="15%", kind="discount")
    ent = Offer.from_offer(real)
    assert ent.code == "FLOWER15" and ent.kind == "discount"

    receipt = SendReceipt.from_action(
        {"id": "act_1", "run_id": "r1", "channel": "sms", "target": "+1", "status": "pending"}
    )
    assert receipt.action_id == "act_1" and receipt.status == "pending"


def test_consent_flags_known_optouts() -> None:
    # The operator's own lead with no opt-out is contactable (the HOLD gate is the real
    # safety); an explicit opt-out or a blocked status is NOT contactable.
    assert Consent.from_facts(_FACTS).contactable is True
    opted = Consent.from_facts({**_FACTS, "opted_out": True})
    assert opted.opted_out is True and opted.contactable is False
    blocked = Consent.from_facts({**_FACTS, "status": "unsubscribed"})
    assert blocked.contactable is False


def test_campaign_from_run_record() -> None:
    rec = type("R", (), {"run_id": "r1", "tenant_id": "t", "status": "completed"})()
    camp = Campaign.from_run(rec)
    assert camp.run_id == "r1" and camp.status == "completed"


def test_not_connected_entities_raise_never_fabricate() -> None:
    with pytest.raises(NotConfiguredError):
        Artist.load("a1")
    with pytest.raises(NotConfiguredError):
        Shop.load("s1")
    with pytest.raises(NotConfiguredError):
        Asset.load("as1")
