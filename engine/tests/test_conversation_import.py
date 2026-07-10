"""Conversation-CSV import: detection, verbatim grouping, opt-out capture.
DB-free — the customer upsert and conversation store are faked at the seam."""

from __future__ import annotations

import studio.conversation_import as ci
from studio.conversation_import import ingest_conversations_csv, is_conversation_csv

CSV = """conversation_ref,customer_name,customer_email,customer_phone,channel,date,time,speaker,sender_label,text
amanda,Amanda Kuhl,amanda@x.test,+1951,sms,2026-01-04,10:48,customer,Amanda Kuhl,Hi there! Wanted to follow up as I am ready to move forward
amanda,Amanda Kuhl,amanda@x.test,+1951,sms,2026-01-04,10:50,studio,Studio,Let's do it!
amanda,Amanda Kuhl,amanda@x.test,+1951,sms,2026-04-23,03:39,customer,Amanda Kuhl,Stop
todd,Todd,todd@x.test,+1702,sms,2026-05-12,07:00,customer,Todd,"Looking for ""self made"" in tall thin letters across my neck. Lemme know, thanks!"
"""


def test_detects_conversation_csv_vs_customer_csv():
    assert is_conversation_csv(CSV)
    assert not is_conversation_csv("name,email\nA,a@x.test\n")


def test_groups_by_customer_keeps_verbatim_text_and_captures_opt_out(monkeypatch):
    stored: dict[str, list[dict]] = {}
    opted: list[str] = []
    monkeypatch.setattr(
        "studio.customer_research.ingest_leads",
        lambda tenant, rows, dsn=None: {
            "ingested": len(rows), "created": len(rows), "matched": 0,
            "customer_ids": [f"cust_{r['email']}" for r in rows],
        },
    )
    monkeypatch.setattr(
        "studio.conversations.upsert_conversation",
        lambda tenant, cid, turns, **kw: stored.__setitem__(cid, turns) or "conv_x",
    )
    monkeypatch.setattr(ci, "_backfill_phone", lambda *a, **k: None)
    monkeypatch.setattr(ci, "_mark_sms_opt_out", lambda t, c, dsn=None: opted.append(c))

    out = ingest_conversations_csv("t_test", CSV)

    assert out["customers"] == 2 and out["conversations"] == 2 and out["turns"] == 4
    amanda = stored["cust_amanda@x.test"]
    # Verbatim, ordered, correctly attributed.
    assert amanda[0] == {"speaker": "customer",
                         "text": "Hi there! Wanted to follow up as I am ready to move forward"}
    assert amanda[1]["speaker"] == "studio"
    # Quoted text survives CSV round-trip exactly.
    todd = stored["cust_todd@x.test"]
    assert todd[0]["text"] == (
        'Looking for "self made" in tall thin letters across my neck. Lemme know, thanks!'
    )
    # Amanda's explicit 'Stop' is captured as an SMS opt-out; Todd is not.
    assert out["opted_out"] == ["cust_amanda@x.test"]
    assert opted == ["cust_amanda@x.test"]
