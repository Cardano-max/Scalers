"""CustomerAcq-nmh.6 part 3: SUPERVISOR memory-state (spec §17) — the voice supervisor
answers "how many customers / which artists / what old campaigns / how many drafts /
what failed / what did we do last time" from REAL stored state, never a guess.

Real-PG on a throwaway tenant."""

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
os.environ.setdefault("SCALERS_EMBEDDER", "deterministic")


def _require_db() -> None:
    try:
        with psycopg.connect(DSN, connect_timeout=3) as conn:
            conn.execute("SELECT 1")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"local Postgres not reachable ({exc})", allow_module_level=True)


_require_db()

from studio.campaign_memory import record_run_campaign  # noqa: E402
from studio.supervisor_memory import (  # noqa: E402
    memory_state,
    what_did_we_do_last_time,
)


def _tenant() -> str:
    return "t_nmh6sup_" + uuid.uuid4().hex[:8]


def _teardown(tenant: str) -> None:
    with psycopg.connect(DSN, autocommit=True) as conn:
        for tbl in ("campaign_examples", "actions", "customers"):
            try:
                conn.execute(f"DELETE FROM {tbl} WHERE tenant_id = %s", (tenant,))
            except Exception:
                pass


def _mk_customer(tenant, i, artist=None):
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO customers (id, tenant_id, name, email, artist, interests, "
            "preferred_channels, email_opt_in, sms_opt_in, source) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (f"{tenant}_c{i}", tenant, f"Lead {i}", f"l{i}@{tenant}.ex", artist,
             [], [], True, False, "test"),
        )


def _mk_action(tenant, i, status):
    from actions.store import ensure_schema, record_pending_action, update_status
    ensure_schema(DSN)
    aid = record_pending_action(
        tenant_id=tenant, decision_id=None, type="outreach", channel="gmail",
        worker="test", target=f"l{i}@x.ex", draft="hi", subject="s",
        conf=None, threshold=None, esc_kind="approval_required", esc_label="x",
        idempotency_key=f"{tenant}:sup:{i}:{status}", run_id=f"{tenant}-run", dsn=DSN,
    )
    if status != "pending":
        update_status(aid, status, dsn=DSN)
    return aid


def test_memory_state_reports_real_counts() -> None:
    tenant = _tenant()
    try:
        for i in range(5):
            _mk_customer(tenant, i, artist="Angel" if i < 3 else "Bea")
        _mk_action(tenant, 0, "pending")
        _mk_action(tenant, 1, "pending")
        _mk_action(tenant, 2, "failed")
        st = memory_state(tenant, dsn=DSN)
        assert st["customers_total"] == 5
        assert set(st["artists"]) == {"Angel", "Bea"}
        assert st["drafts"]["pending"] == 2  # review queue
        assert st["drafts"]["failed"] == 1
    finally:
        _teardown(tenant)


def test_memory_state_scoped_to_artist_lists_their_campaigns() -> None:
    tenant = _tenant()
    try:
        _mk_customer(tenant, 0, artist="Angel")
        record_run_campaign(tenant, campaign_name="Angel Full-Day", artist="Angel",
                            offer_type="full-day", offer_price_usd=1200,
                            cta="Reply ANGEL", recipient_count=994, run_id="r1", dsn=DSN)
        st = memory_state(tenant, artist="Angel", dsn=DSN)
        assert st["artist"] == "Angel"
        assert st["campaigns_for_artist"] == 1
        assert st["last_campaign_summary"] and "1200" in st["last_campaign_summary"]
    finally:
        _teardown(tenant)


def test_what_did_we_do_last_time_answers_from_stored_memory() -> None:
    tenant = _tenant()
    try:
        record_run_campaign(tenant, campaign_name="Angel $1200 Full-Day", artist="Angel",
                            offer_type="full-day special", offer_price_usd=1200,
                            cta="Reply ANGEL", recipient_count=994, delivered_count=980,
                            run_id="r1", dsn=DSN)
        ans = what_did_we_do_last_time(tenant, "Angel", dsn=DSN)
        assert ans is not None
        assert "Angel" in ans and "1200" in ans and "994" in ans
        # Honest when nothing is stored — never a guessed campaign.
        assert what_did_we_do_last_time(tenant, "Nobody", dsn=DSN) is None
    finally:
        _teardown(tenant)


def test_memory_state_empty_tenant_is_honest_zeroes() -> None:
    tenant = _tenant()
    try:
        st = memory_state(tenant, dsn=DSN)
        assert st["customers_total"] == 0
        assert st["artists"] == []
        assert st["drafts"]["pending"] == 0
    finally:
        _teardown(tenant)


# ── AC end-to-end: a real run records campaign memory; run 2 remembers run 1 ── #


def test_real_run_records_campaign_memory_and_run2_remembers_run1() -> None:
    """nmh.6 AC: run a campaign for an artist; run a 2nd campaign same artist; the
    supervisor answers 'what did we do last time' from STORED campaign memory (written
    automatically by the run), and it persists across a fresh connection."""
    import os
    os.environ["SCALERS_OUTREACH_LLM"] = "0"
    from studio.agui import CampaignPlan, _execute_provided_leads_sync

    tenant = _tenant()
    try:
        ids = []
        with psycopg.connect(DSN, autocommit=True) as conn:
            for i in range(3):
                cid = f"{tenant}_ang{i}"
                conn.execute(
                    "INSERT INTO customers (id, tenant_id, name, email, artist, "
                    "interests, preferred_channels, email_opt_in, sms_opt_in, source) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (cid, tenant, f"Fan {i}", f"fan{i}@{tenant}.ex", "Angel",
                     [], [], True, False, "test"),
                )
                ids.append(cid)

        def _run():
            plan = CampaignPlan(
                lead_source="provided", goal="Angel full-day special", channels=["gmail"],
                output_count=3,
                customers={"customer_ids": ids, "rows": 3, "columns": ["name", "email"]},
            )
            return _execute_provided_leads_sync(plan, "sess", tenant, DSN, None)

        _run()  # campaign 1
        _run()  # campaign 2 (same artist)

        # The supervisor answers from stored memory (written by the runs, not a guess).
        ans = what_did_we_do_last_time(tenant, "Angel", dsn=DSN)
        assert ans is not None and "Angel" in ans

        # Persists across a FRESH connection (restart proxy) and both runs are recorded.
        from studio.campaign_memory import campaigns_for_artist
        assert len(campaigns_for_artist(tenant, "Angel", dsn=DSN)) == 2

        st = memory_state(tenant, artist="Angel", dsn=DSN)
        assert st["customers_total"] == 3
        assert st["campaigns_for_artist"] == 2
        assert st["review_queue"] >= 3  # the staged drafts are in the Review Queue
    finally:
        _teardown(tenant)
        with psycopg.connect(DSN, autocommit=True) as conn:
            for tbl in ("lead_conversations", "memories", "runs"):
                try:
                    conn.execute(f"DELETE FROM {tbl} WHERE tenant_id = %s", (tenant,))
                except Exception:
                    pass
