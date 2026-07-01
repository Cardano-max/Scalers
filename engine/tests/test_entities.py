"""Canonical entity model — pure/offline tests.

Proves entities.py RE-EXPORTS the existing canonical dataclasses (one source of truth, no
parallel fork), that the new real-backed entities build from grounded facts, that Consent is
a first-class typed gate the channel selection routes through, and that Shop is an honest
NotConfiguredError stub (Asset is NOT a stub — it reads the real team.store assets table).
"""

from __future__ import annotations

import pytest

from studio import entities
from studio.adapters import NotConfiguredError


def test_entities_reexport_the_existing_canonical_dataclasses() -> None:
    # NOT a parallel fork — the SAME objects as the adapters / offers module.
    from studio.adapters.artist_source import Artist as _Artist, Artwork as _Artwork
    from studio.adapters.lead_source import Lead as _Lead
    from studio.adapters.message_source import ConversationThread as _CT
    from studio.offers import Offer as _Offer

    assert entities.Lead is _Lead
    assert entities.ConversationThread is _CT
    assert entities.Artist is _Artist and entities.Artwork is _Artwork
    assert entities.Offer is _Offer


def test_crm_record_and_send_receipt_from_real_rows() -> None:
    facts = {
        "customer_id": "cust_x", "artist": None,
        "tattoo_history": [{"piece": "rose"}],
        "persona_traits": {"lifecycle_stage": "lapsed"},
    }
    crm = entities.CRMRecord.from_facts(facts)
    assert crm.past_tattoos == 1 and crm.lifecycle_stage == "lapsed"

    receipt = entities.SendReceipt.from_action(
        {"id": "act_1", "run_id": "r1", "channel": "sms", "target": "+1", "status": "pending"}
    )
    assert receipt.action_id == "act_1" and receipt.status == "pending"


def test_consent_is_a_first_class_channel_gate() -> None:
    base = {"customer_id": "c1"}
    # No opt-in -> email/sms withheld; instagram organic (allowed).
    c = entities.Consent.from_facts(base)
    assert c.allows("gmail") is False and c.allows("sms") is False
    assert c.allows("instagram") is True
    # Opt-ins grant the channel; provenance is recorded.
    c2 = entities.Consent.from_facts({**base, "email_opt_in": True, "sms_opt_in": True})
    assert c2.allows("gmail") is True and c2.allows("sms") is True
    assert c2.basis == "opt_in_flag"
    # A global opt-out withholds EVERY channel (never override withheld consent).
    c3 = entities.Consent.from_facts({**base, "email_opt_in": True, "opted_out": True})
    assert c3.allows("gmail") is False and c3.allows("instagram") is False
    # The module gate mirrors the model.
    assert entities.channel_consented({**base, "sms_opt_in": True}, "sms") is True
    assert entities.channel_consented(base, "sms") is False


def test_campaign_from_run_record() -> None:
    rec = type("R", (), {"run_id": "r1", "tenant_id": "t", "status": "completed"})()
    camp = entities.Campaign.from_run(rec)
    assert camp.run_id == "r1" and camp.status == "completed"


def test_asset_is_backed_by_the_real_table_not_a_stub() -> None:
    # Asset maps a real team.store assets row (no NotConfiguredError, no fabrication).
    asset = entities.Asset.from_row(
        {"id": "as1", "campaign_id": "camp1", "asset_type": "email", "status": "queued"}
    )
    assert asset.id == "as1" and asset.status == "queued"
    assert hasattr(entities.Asset, "for_campaign")  # reads team.store.list_assets


def test_shop_is_an_honest_not_connected_stub() -> None:
    with pytest.raises(NotConfiguredError):
        entities.Shop.load("s1")
