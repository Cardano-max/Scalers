"""nmh.11 FAIL-FIRST regression + Angel-1200 harness (provided-leads actions path).

eng4 SUPPORT for eng3 (who owns the fix). These tests MUST FAIL on the unpatched
6310f8b code and PASS once eng3 lands: (a) cohort tiebreaker, (b) partial-unique
actions(tenant_id, worker, target) WHERE status='pending' guard, (c) cap-decouple.

Two properties (the AC):
  1. RETRY STABILITY — staging the same cohort twice (each run mints its own run_id,
     which is the real-world "retry") must NOT duplicate a recipient in the pending
     review queue. Unpatched: the 2nd run re-stages N NEW pending rows under a new
     run_id -> 2N rows / duplicate targets = the "phantom duplicate / vanish" bug.
  2. EXACTLY-N — N in {10, 25, 30} must stage exactly N distinct-recipient gmail
     drafts, is_seeded=false. Unpatched: _OUTPUT_HARD_CAP=12 clips 25/30 to 12.

Real local Postgres; throwaway tenant per test, cleaned up.
"""

from __future__ import annotations

import os
import uuid

import psycopg
import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.getenv("ENGINE_DATABASE_URL"), reason="requires Postgres"
    ),
]

DSN = os.environ.get("ENGINE_DATABASE_URL", "postgresql://scalers:scalers@localhost:5432/scalers")


def _seed(tenant: str, n: int) -> None:
    """Seed N contactable customers (distinct emails, email_opt_in=True via bool(email))."""
    from studio.customer_research import upsert_lead

    for i in range(n):
        upsert_lead(tenant, {"name": f"Cust {i}", "email": f"c{i}.{tenant}@example.com"}, dsn=DSN)


def _run(tenant: str, n: int) -> str:
    """Stage N gmail win-back drafts via the provided-leads path; return the run_id."""
    from studio.agui import CampaignPlan, _execute_provided_leads_sync

    plan = CampaignPlan(
        lead_source="provided",
        goal="Angel full-day special — $1,200 (win back lapsed clients)",
        channels=["gmail"],
        output_count=n,
        lead_count=n,
        customers={"rows": n, "columns": ["name", "email"]},
    )
    summary = _execute_provided_leads_sync(plan, f"sess-{uuid.uuid4().hex[:8]}", tenant, DSN, None)
    return summary["run_id"]


def _pending_counts(tenant: str) -> tuple[int, int]:
    with psycopg.connect(DSN, autocommit=True) as c:
        row = c.execute(
            "SELECT count(*), count(DISTINCT target) FROM actions "
            "WHERE tenant_id=%s AND status='pending' AND channel='gmail'",
            (tenant,),
        ).fetchone()
    return row[0], row[1]


def _cleanup(tenant: str) -> None:
    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute("DELETE FROM actions WHERE tenant_id=%s", (tenant,))
        c.execute("DELETE FROM lead_conversations WHERE tenant_id=%s", (tenant,))
        c.execute("DELETE FROM customers WHERE tenant_id=%s", (tenant,))


@pytest.fixture()
def env(monkeypatch):
    monkeypatch.setenv("SCALERS_OUTREACH_LLM", "0")  # deterministic drafting, no LLM
    monkeypatch.setenv("SCALERS_EMBEDDER", "deterministic")
    tenant = f"nmh11_{uuid.uuid4().hex[:10]}"
    try:
        yield tenant
    finally:
        _cleanup(tenant)


def test_retry_with_new_run_id_does_not_duplicate_recipients(env):
    """FAIL-FIRST: two stagings of the same 10-customer cohort (each a fresh run_id =
    a real retry) must leave EXACTLY 10 distinct-recipient pending rows, not 20."""
    tenant, n = env, 10
    _seed(tenant, n)
    _run(tenant, n)
    _run(tenant, n)  # the "retry" — a second run_id for the same cohort

    total, distinct = _pending_counts(tenant)
    assert distinct == n, f"expected {n} distinct recipients, got {distinct}"
    assert total == n, (
        f"retry re-staged phantom duplicates: {total} pending gmail rows for {n} "
        f"customers (a retry must not duplicate an already-pending recipient)"
    )


@pytest.mark.parametrize("n", [10, 25, 30])
def test_exactly_n_distinct_gmail_drafts(env, n):
    """FAIL-FIRST for N=25/30 (cap clips to 12): a run must stage exactly N distinct
    real-recipient gmail drafts, is_seeded=false."""
    tenant = env
    _seed(tenant, n)
    run_id = _run(tenant, n)

    with psycopg.connect(DSN, autocommit=True) as c:
        row = c.execute(
            "SELECT count(*), count(DISTINCT target), "
            "bool_and(target IS NOT NULL AND target <> ''), bool_and(is_seeded = false) "
            "FROM actions WHERE run_id=%s AND channel='gmail'",
            (run_id,),
        ).fetchone()
    count, distinct, targets_real, all_live = row
    assert count == n, f"N={n}: expected {n} gmail drafts, got {count} (cap/skip clipped it)"
    assert distinct == n, f"N={n}: expected {n} distinct recipients, got {distinct}"
    assert targets_real, f"N={n}: some actions.target is null/blank (not a real recipient)"
    assert all_live, f"N={n}: some action is_seeded=true (must be live work)"
