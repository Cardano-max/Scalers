"""Conversation-CSV import: detection, verbatim grouping, opt-out capture.
DB-free — the customer upsert and conversation store are faked at the seam
(one DB-gated test covers the shared-phone name disambiguation SQL)."""

from __future__ import annotations

import os
import uuid

import pytest

import studio.conversation_import as ci
from studio.conversation_import import ingest_conversations_csv, is_conversation_csv

_pg = pytest.mark.skipif(
    not os.environ.get("ENGINE_DATABASE_URL"),
    reason="requires Postgres (set ENGINE_DATABASE_URL)",
)

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


def test_placeholder_email_resolves_to_real_identity_on_file(monkeypatch):
    """A transcript keyed by a .test placeholder email must land on the customer
    already on file with the same phone (e.g. from the appointment import) —
    one person, one row, never a parallel identity."""
    seen_rows: list[dict] = []
    monkeypatch.setattr(
        "studio.customer_research.ingest_leads",
        lambda tenant, rows, dsn=None: seen_rows.extend(rows) or {
            "ingested": len(rows), "customer_ids": [f"cust_{r['email']}" for r in rows],
        },
    )
    monkeypatch.setattr(
        "studio.conversations.upsert_conversation", lambda *a, **k: "conv_x"
    )
    monkeypatch.setattr(ci, "_backfill_phone", lambda *a, **k: None)
    monkeypatch.setattr(ci, "_mark_sms_opt_out", lambda *a, **k: None)
    # On file: same phone, REAL email (appointment-history row).
    monkeypatch.setattr(
        ci, "_email_for_phone",
        lambda tenant, phone, name="", dsn=None: (
            "lindsey.real@gmail.com" if phone == "+1725" else ""
        ),
    )

    csv_text = (
        "customer_name,customer_email,customer_phone,channel,speaker,text\n"
        "Lindsey Ledesma,lindsey.demo@skindesign.test,+1725,sms,customer,Gotcha\n"
        "No Match,nomatch.demo@skindesign.test,+1999,sms,customer,Hello\n"
    )
    out = ingest_conversations_csv("t_test", csv_text)

    by_name = {r["name"]: r["email"] for r in seen_rows}
    # Placeholder replaced by the canonical identity on file...
    assert by_name["Lindsey Ledesma"] == "lindsey.real@gmail.com"
    # ...but with no phone match the placeholder stays (still one honest row).
    assert by_name["No Match"] == "nomatch.demo@skindesign.test"
    assert out["customers"] == 2


@pytest.mark.integration
@_pg
def test_shared_phone_resolves_by_name_match():
    """Two real customers on ONE phone (couples booking): the thread lands on
    the person the export names, not the oldest row."""
    import psycopg

    dsn = os.environ["ENGINE_DATABASE_URL"]
    tenant = "t_convtest_" + uuid.uuid4().hex[:6]
    phone = "+1555" + uuid.uuid4().hex[:7]
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO customers (id, tenant_id, name, email, phone) VALUES "
            "('cst_a_' || %s, %s, 'Jessica Example', 'jessica.example.real@gmail.test.x', %s), "
            "('cst_b_' || %s, %s, 'Kassie Example', 'kassie.example.real@gmail.test.x', %s)",
            (tenant, tenant, phone, tenant, tenant, phone),
        )
    try:
        # NB: emails end in .x so the placeholder exclusion doesn't drop them.
        assert ci._email_for_phone(tenant, phone, "Kassie Example", dsn=dsn) == (
            "kassie.example.real@gmail.test.x"
        )
        assert ci._email_for_phone(tenant, phone, "Jessica Example", dsn=dsn) == (
            "jessica.example.real@gmail.test.x"
        )
        # Unknown name → deterministic oldest row, never an error.
        assert ci._email_for_phone(tenant, phone, "Somebody Else", dsn=dsn) != ""
    finally:
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute("DELETE FROM customers WHERE tenant_id=%s", (tenant,))
