"""Appointment-CSV import: detection, per-customer grouping, honest NULLs, and
idempotent (tenant, appointment_id, slot_date) session keys. The pure tests fake
the customer-upsert / store / memory seams; the last test hits a real Postgres
(skipped without one) and proves a re-upload writes nothing new.
"""

from __future__ import annotations

import os

import pytest

import studio.appointment_import as ai
from studio.appointment_import import ingest_appointments_csv, is_appointment_csv

CSV = """appointment_id,status,tattoo_description,style,size,placement,deposit,total,internalNote,slot_id,slot_date,slot_time,duration,slot_type,quotedAmount,slot_title,tbd,customer_name,customer_email,customer_phone
APT-1,confirmed,Fine-line rose with stem,fine-line,medium,forearm,150,600,"prefers weekends, pays cash",S-1,2026-01-10,10:00,180,session,600,Session 1,,Amanda Kuhl,amanda@x.test,+1951
APT-1,confirmed,Fine-line rose with stem,fine-line,medium,forearm,150,600,"prefers weekends, pays cash",S-2,2026-02-14,10:00,180,session,600,Session 2,,Amanda Kuhl,amanda@x.test,+1951
APT-1,confirmed,Fine-line rose with stem,fine-line,medium,forearm,150,600,"prefers weekends, pays cash",S-2,2026-02-14,10:00,180,session,600,Session 2,,Amanda Kuhl,amanda@x.test,+1951
APT-2,completed,,,,,,,,S-3,2026-03-01,12:00,120,session,TBD,Session 1,,Todd,,+1702
"""

# The other export shape: date/time/type/title instead of slot_*.
CSV_VARIANT = (
    "appointment_id,status,style,deposit,date,time,type,title,customer_name,customer_email\n"
    "APT-9,completed,realism,200,2026-05-02,09:00,session,Sleeve day 1,Mo,mo@x.test\n"
)


def test_detects_appointment_csv_vs_customer_and_conversation_csv():
    assert is_appointment_csv(CSV)
    assert is_appointment_csv(CSV_VARIANT)  # date/time/type/title variant
    assert not is_appointment_csv("name,email\nA,a@x.test\n")
    # A conversation export has customer_email + date but no appointment_id.
    assert not is_appointment_csv(
        "conversation_ref,customer_name,customer_email,date,speaker,text\n"
        "amanda,Amanda,amanda@x.test,2026-01-04,customer,Hi\n"
    )
    assert not is_appointment_csv("")


def _patch_seams(monkeypatch, store: dict, memories: list):
    monkeypatch.setattr(
        "studio.customer_research.ingest_leads",
        lambda tenant, rows, dsn=None: {
            "ingested": len(rows), "created": len(rows), "matched": 0,
            "customer_ids": [f"cust_{r['email'] or r['name']}" for r in rows],
        },
    )

    def fake_persist(tenant, sessions, dsn=None):
        inserted = 0
        for s in sessions:
            k = (tenant, s["appointment_id"], s["slot_date"])
            if k not in store:
                store[k] = s
                inserted += 1
        return inserted, len(sessions) - inserted

    monkeypatch.setattr(ai, "_persist_sessions", fake_persist)
    monkeypatch.setattr(
        ai, "_write_customer_memory",
        lambda tenant, cid, text, metadata, dsn=None: (
            memories.append((cid, text, metadata)) or "mem_x"
        ),
    )
    monkeypatch.setattr(ai, "_find_customer", lambda *a, **k: None)
    monkeypatch.setattr(ai, "_backfill_phone", lambda *a, **k: None)


def test_groups_by_customer_keeps_nulls_and_reingest_inserts_nothing(monkeypatch):
    store: dict[tuple, dict] = {}
    memories: list[tuple[str, str, dict]] = []
    _patch_seams(monkeypatch, store, memories)

    out = ingest_appointments_csv("t_test", CSV)

    # 4 rows -> 3 session days (one exact in-file repeat), 2 appointments, 2 customers.
    assert out["customers"] == 2 and out["appointments"] == 2 and out["sessions"] == 3
    assert out["sessions_inserted"] == 3 and out["sessions_existing"] == 0
    assert out["customer_ids"] == ["cust_amanda@x.test", "cust_Todd"]

    # HONEST NULLs: Todd's blanks stay None and "TBD" never becomes a number.
    todd = store[("t_test", "APT-2", "2026-03-01")]
    assert todd["style"] is None and todd["deposit"] is None
    assert todd["quoted_amount"] is None and todd["internal_note"] is None
    # Internal notes are verbatim; amounts parse off the row.
    amanda = store[("t_test", "APT-1", "2026-01-10")]
    assert amanda["internal_note"] == "prefers weekends, pays cash"
    assert float(amanda["deposit"]) == 150.0

    # Artist performance: deposits counted per APPOINTMENT (150 once, not per row).
    perf = out["performance"]
    assert perf["sessions"] == 3 and perf["unique_customers"] == 2
    assert perf["total_deposits"] == 150.0
    assert perf["date_span"] == {"from": "2026-01-10", "to": "2026-03-01"}

    # ONE dossier-visible memory per customer, built only from real CSV facts.
    assert out["memories"] == 2 and [m[0] for m in memories] == out["customer_ids"]
    amanda_text = memories[0][1]
    assert "between 2026-01-10 and 2026-02-14" in amanda_text
    assert "fine-line" in amanda_text and "150.00" in amanda_text
    todd_text = memories[1][1]
    assert "fine-line" not in todd_text and "deposits recorded" not in todd_text

    # Re-upload: the same natural keys hit the store again -> nothing inserted.
    again = ingest_appointments_csv("t_test", CSV)
    assert again["sessions_inserted"] == 0 and again["sessions_existing"] == 3
    assert again["customer_ids"] == out["customer_ids"]


# --------------------------------------------------------------------------- #
# Postgres integration (skips without a DB) — the real ON CONFLICT natural key.
# --------------------------------------------------------------------------- #

_DSN = (
    os.environ.get("ENGINE_DATABASE_URL")
    or os.environ.get("DATABASE_URL")
    or "postgresql://scalers:scalers@localhost:5432/scalers"
)


def _db_or_skip():
    try:
        import psycopg

        psycopg.connect(_DSN, connect_timeout=3).close()
    except Exception as exc:  # pragma: no cover - env-dependent
        pytest.skip(f"no Postgres for integration test: {exc}")


def test_reingest_writes_no_duplicate_sessions_customers_or_memories(monkeypatch):
    _db_or_skip()
    monkeypatch.setenv("SCALERS_EMBEDDER", "deterministic")
    import uuid

    import psycopg

    tag = uuid.uuid4().hex[:8]
    tenant = f"t_appt_it_{tag}"
    csv_text = (
        "appointment_id,status,tattoo_description,style,size,placement,deposit,total,"
        "internalNote,slot_id,slot_date,slot_time,duration,slot_type,quotedAmount,"
        "slot_title,tbd,customer_name,customer_email,customer_phone\n"
        f"A{tag}1,confirmed,rose on forearm,fine-line,medium,forearm,150,600,,S1,"
        f"2026-01-10,10:00,180,session,600,Session 1,,Amanda K,amanda.{tag}@x.test,+1951\n"
        f"A{tag}1,confirmed,rose on forearm,fine-line,medium,forearm,150,600,,S2,"
        f"2026-02-14,10:00,180,session,600,Session 2,,Amanda K,amanda.{tag}@x.test,+1951\n"
        f"A{tag}2,completed,script collarbone,,small,collarbone,,,paid cash,S3,"
        f"2026-03-01,12:00,120,session,,Session 1,,Todd {tag},,+1702{tag}\n"
    )
    try:
        first = ingest_appointments_csv(tenant, csv_text, dsn=_DSN)
        second = ingest_appointments_csv(tenant, csv_text, dsn=_DSN)

        assert first["sessions_inserted"] == 3 and first["memories"] == 2
        assert second["sessions_inserted"] == 0 and second["sessions_existing"] == 3
        # Email-keyed AND phone-only customers both resolve to the same rows again.
        assert second["customer_ids"] == first["customer_ids"]

        with psycopg.connect(_DSN) as conn:
            n_sessions = conn.execute(
                "SELECT count(*) FROM appointments WHERE tenant_id=%s", (tenant,)
            ).fetchone()[0]
            n_customers = conn.execute(
                "SELECT count(*) FROM customers WHERE tenant_id=%s", (tenant,)
            ).fetchone()[0]
            n_memories = conn.execute(
                "SELECT count(*) FROM memories WHERE tenant_id=%s "
                "AND subject_type='customer'",
                (tenant,),
            ).fetchone()[0]
        assert n_sessions == 3 and n_customers == 2 and n_memories == 2
    finally:  # leave the shared DB as found (unique throwaway tenant regardless)
        with psycopg.connect(_DSN, autocommit=True) as conn:
            conn.execute("DELETE FROM appointments WHERE tenant_id=%s", (tenant,))
            conn.execute("DELETE FROM memories WHERE tenant_id=%s", (tenant,))
            conn.execute("DELETE FROM customers WHERE tenant_id=%s", (tenant,))
