"""WAVE0 (CustomerAcq-nmh.1): draft-count exactness — spec §14, NON-NEGOTIABLE.

Request N -> EXACTLY N drafts (one per valid contact), or =valid-contacts with an
explicit reconciled skip for the shortfall. Never a self-chosen number, never a
silent drop.

The bug (reproduced): the cohort path (no explicit ``customer_ids``) sized the run to
the WARM cohort only (``conversation_leads``) — a tenant with 7 conversation leads but
80+ contactable customers produced 7 drafts for a request of 10, with a "3 short" row
and ``reconciled=False``. Spec §14: ask for 10 with >=10 valid contacts -> 10 drafts.

Runs against the REAL local Postgres on a THROWAWAY tenant (created + torn down here)
so the counts are deterministic and independent of seeded data. Keyless/offline
(``SCALERS_OUTREACH_LLM=0``) so the copywriter path is the deterministic template.
"""

from __future__ import annotations

import os
import uuid

import psycopg
import pytest

# Resolve the DSN FROM the environment — never write it INTO os.environ:
# pytest imports every module at collection time, and a setdefault here
# un-skips every other module whose skipif guards on ENGINE_DATABASE_URL
# being unset (the DB-free unit lane then dies with connection errors).
DSN = os.environ.get(
    "ENGINE_DATABASE_URL", "postgresql://scalers:scalers@localhost:5432/scalers"
)
os.environ.setdefault("SCALERS_OUTREACH_LLM", "0")
os.environ.setdefault("SCALERS_EMBEDDER", "deterministic")


def _require_db() -> None:
    try:
        with psycopg.connect(DSN, connect_timeout=3) as conn:
            conn.execute("SELECT 1")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"local Postgres not reachable ({exc})", allow_module_level=True)


_require_db()

from studio.agui import CampaignPlan, _execute_provided_leads_sync  # noqa: E402
from studio.conversations import upsert_conversation  # noqa: E402


def _seed_tenant(n_contactable: int, n_warm: int) -> str:
    """A throwaway tenant with ``n_contactable`` customers that all have an email, of
    which ``n_warm`` also have a stored conversation (so they are the WARM cohort)."""
    tenant = "t_nmh1_" + uuid.uuid4().hex[:8]
    with psycopg.connect(DSN, autocommit=True) as conn:
        for i in range(n_contactable):
            cid = f"{tenant}_c{i:03d}"
            conn.execute(
                "INSERT INTO customers (id, tenant_id, name, email, interests, "
                "preferred_channels, email_opt_in, sms_opt_in, source) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (cid, tenant, f"Lead {i}", f"lead{i}@{tenant}.example",
                 [], [], True, False, "test_nmh1"),
            )
            if i < n_warm:
                upsert_conversation(
                    tenant, cid,
                    [{"speaker": "customer", "text": "maybe later, bit tight on budget"}],
                    channel="sms", source="upload", dsn=DSN,
                )
    return tenant


def _teardown(tenant: str) -> None:
    with psycopg.connect(DSN, autocommit=True) as conn:
        for tbl in ("lead_conversations", "memories", "actions",
                    "customer_personas", "customers"):
            try:
                conn.execute(f"DELETE FROM {tbl} WHERE tenant_id = %s", (tenant,))
            except Exception:
                pass


def _run(tenant: str, requested: int) -> dict:
    plan = CampaignPlan(
        lead_source="provided", goal="win back lapsed clients", channels=["gmail"],
        output_count=requested, lead_count=requested,
        customers={"rows": requested, "columns": ["name", "email"]},
    )
    return _execute_provided_leads_sync(plan, "sess_nmh1", tenant, DSN, None)


def test_cohort_fills_to_exactly_n_when_enough_valid_contacts() -> None:
    """8 contactable (3 warm) + request 6 -> EXACTLY 6 drafts, 0 shortfall, reconciled."""
    tenant = _seed_tenant(n_contactable=8, n_warm=3)
    try:
        led = _run(tenant, 6)["output_ledger"]
        assert led["expected"] == 6
        assert led["drafted"] == 6, f"requested 6, got {led['drafted']}"
        assert led["reconciled"] is True
        # No shortfall skip: there were enough valid contacts to fill the ask.
        assert not any("short of requested" in s["reason"] for s in led["skipped"])
    finally:
        _teardown(tenant)


def test_cohort_request_equals_all_valid_contacts() -> None:
    """5 contactable (2 warm) + request 5 -> exactly 5 drafts (warm topped up)."""
    tenant = _seed_tenant(n_contactable=5, n_warm=2)
    try:
        led = _run(tenant, 5)["output_ledger"]
        assert led["drafted"] == 5, f"requested 5, got {led['drafted']}"
        assert led["reconciled"] is True
    finally:
        _teardown(tenant)


def test_cohort_shortfall_is_reconciled_with_a_counted_reason() -> None:
    """Genuinely fewer valid contacts than requested: 4 contactable + request 10 ->
    4 drafts + a shortfall that ACCOUNTS for the missing 6, so the ledger reconciles
    (spec: requested=10, valid=4, created=4, skipped=6 with reason)."""
    tenant = _seed_tenant(n_contactable=4, n_warm=1)
    try:
        led = _run(tenant, 10)["output_ledger"]
        assert led["expected"] == 10
        assert led["drafted"] == 4, f"only 4 valid contacts, got {led['drafted']}"
        # The shortfall is accounted for (as a count), so the run reconciles honestly.
        assert led["reconciled"] is True
        shortfall = [s for s in led["skipped"] if "short of requested" in s["reason"]]
        assert len(shortfall) == 1
        assert shortfall[0].get("count") == 6  # 10 requested - 4 valid
    finally:
        _teardown(tenant)


def test_request_above_output_cap_reconciles_with_an_honest_cap_reason(monkeypatch) -> None:
    """A request above the output hard cap is clipped BY THE CAP, not falsely
    reported as a contact shortage: 20 contactable + request 20 -> 12 drafts + a skip
    that says 8 are beyond the cap (honest cause), and the ledger reconciles.

    nmh.11 decoupled the provided-leads bound from the compose spine's cap
    (ENGINE_COHORT_HARD_CAP, default 1000) — pin it to 12 so the honest-cap-reason
    contract stays exactly verified without seeding 1000+ contacts."""
    _OUTPUT_HARD_CAP = 12
    monkeypatch.setenv("ENGINE_COHORT_HARD_CAP", str(_OUTPUT_HARD_CAP))

    tenant = _seed_tenant(n_contactable=_OUTPUT_HARD_CAP + 8, n_warm=2)
    try:
        led = _run(tenant, _OUTPUT_HARD_CAP + 8)["output_ledger"]
        assert led["drafted"] == _OUTPUT_HARD_CAP  # cap-limited, but FULL to the cap
        assert led["reconciled"] is True
        cap_rows = [s for s in led["skipped"] if "beyond the output cap" in s["reason"]]
        assert len(cap_rows) == 1 and cap_rows[0]["count"] == 8
        # Not falsely blamed on contact supply — the tenant HAS the contacts.
        assert not any("contactable customer" in s["reason"] for s in led["skipped"])
    finally:
        _teardown(tenant)


def test_no_valid_contacts_is_honest_zero() -> None:
    """A tenant with no contactable customers drafts nothing, reconciled with a reason
    (never a fabricated draft, never a crash)."""
    tenant = "t_nmh1_empty_" + uuid.uuid4().hex[:8]
    try:
        led = _run(tenant, 5)["output_ledger"]
        assert led["drafted"] == 0
        assert led["reconciled"] is True
    finally:
        _teardown(tenant)
