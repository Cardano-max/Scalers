"""tlv.6 demo-tenant persona seeder — parse + seeding logic.

Parsing is pure; seeding is verified with the customer/conversation stores mocked
(the real PG round-trip is exercised by the end-to-end demo once #116 lands), so
this stays fast and DB-free while proving the CSV->persona->upsert mapping.
"""

from __future__ import annotations

from datetime import date

import pytest

CSV = """name,email,phone,interests,last_visit,objection,preferred_artist,notes
Marcus Bell,Marcus.Bell@example.com,+13035550142,color realism;wildlife,2024-12-05,Got busy and never rebooked,Nova Reyes,Half-sleeve in progress
Ava Sinclair,ava.sinclair@example.com,+13035550118,fine-line;botanical,2026-07-01,Nervous about pain,Theo Marsh,Consult done
No Email,,+13035550100,blackwork,2024-01-01,,,
"""


@pytest.fixture
def csv_file(tmp_path):
    p = tmp_path / "customers.csv"
    p.write_text(CSV, encoding="utf-8")
    return p


def test_parse_personas_skips_no_email_and_splits_fields(csv_file):
    from studio.demo_seed import parse_demo_personas

    personas = parse_demo_personas(csv_file)
    assert [p.email for p in personas] == [
        "marcus.bell@example.com",  # email lowercased
        "ava.sinclair@example.com",
    ]  # the no-email row is skipped
    marcus = personas[0]
    assert marcus.interests == ["color realism", "wildlife"]
    assert marcus.preferred_artist == "Nova Reyes"
    assert marcus.objection == "Got busy and never rebooked"


def test_is_lapsed_uses_last_visit(csv_file):
    from studio.demo_seed import parse_demo_personas

    marcus, ava = parse_demo_personas(csv_file)
    today = date(2026, 7, 9)
    assert marcus.is_lapsed(today=today) is True  # last visit 2024-12-05
    assert ava.is_lapsed(today=today) is False  # last visit 2026-07-01 (8 days)


def test_seed_maps_rows_and_grounds_objection(csv_file, monkeypatch):
    import studio.conversations as conversations
    import studio.customer_research as customer_research

    lead_calls: list[tuple[str, dict]] = []
    conv_calls: list[tuple] = []

    def fake_upsert_lead(tenant_id, row, *, dsn=None):
        lead_calls.append((tenant_id, row))
        return {"customer_id": f"cust_{row['email']}", "created": True}

    def fake_upsert_conversation(tenant_id, cid, turns, *, channel, source, campaign_message=None, dsn=None):
        conv_calls.append((tenant_id, cid, turns, source))

    monkeypatch.setattr(customer_research, "upsert_lead", fake_upsert_lead)
    monkeypatch.setattr(conversations, "upsert_conversation", fake_upsert_conversation)

    from studio.demo_seed import seed_demo_studio

    summary = seed_demo_studio("demo_studio", csv_path=csv_file)

    assert summary["personas"] == 2
    assert summary["conversations"] == 2  # both personas carry an objection
    assert summary["errors"] == []
    assert len(summary["customer_ids"]) == 2

    # The rich columns are mapped into the upsert row (not dropped).
    _, marcus_row = lead_calls[0]
    assert marcus_row["artist"] == "Nova Reyes"
    assert marcus_row["interests"] == "color realism; wildlife"
    assert marcus_row["customer_type"] == "lapsed"
    assert "last visit 2024-12-05" in marcus_row["notes"]

    # The objection is grounded as a real conversation turn (not fabricated by us).
    _, _, turns, source = conv_calls[0]
    assert source == "demo_seed"
    assert turns[0] == {"speaker": "customer", "text": "Got busy and never rebooked"}


def test_seed_is_best_effort_per_persona(csv_file, monkeypatch):
    import studio.conversations as conversations
    import studio.customer_research as customer_research

    def boom_on_ava(tenant_id, row, *, dsn=None):
        if "ava" in row["email"]:
            raise RuntimeError("db blip")
        return {"customer_id": "cust_marcus", "created": True}

    monkeypatch.setattr(customer_research, "upsert_lead", boom_on_ava)
    monkeypatch.setattr(conversations, "upsert_conversation", lambda *a, **k: None)

    from studio.demo_seed import seed_demo_studio

    summary = seed_demo_studio("demo_studio", csv_path=csv_file)
    assert len(summary["customer_ids"]) == 1  # marcus seeded
    assert len(summary["errors"]) == 1  # ava surfaced, not aborted
    assert "ava" in summary["errors"][0]["email"]
