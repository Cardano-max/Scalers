"""CustomerAcq-nmh.6 part 1: CAMPAIGN MEMORY library (spec §18) — our own completed
runs are stored as durable campaign memory and REUSED for the next campaign
('last time we ran X for this artist'), alongside operator-imported examples.

Real-PG on a throwaway tenant (torn down here). Persistence is proven by reading
back through a FRESH connection (a restart proxy)."""

from __future__ import annotations

import uuid

import psycopg
import pytest

DSN = "postgresql://scalers:scalers@localhost:5432/scalers"


def _require_db() -> None:
    try:
        with psycopg.connect(DSN, connect_timeout=3) as conn:
            conn.execute("SELECT 1")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"local Postgres not reachable ({exc})", allow_module_level=True)


_require_db()

from studio.campaign_memory import (  # noqa: E402
    SOURCE_ENGINE_RUN,
    campaigns_for_artist,
    last_campaign,
    record_run_campaign,
    summarize_last,
)


def _tenant() -> str:
    return "t_nmh6cm_" + uuid.uuid4().hex[:8]


def _teardown(tenant: str) -> None:
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("DELETE FROM campaign_examples WHERE tenant_id = %s", (tenant,))


def test_record_run_campaign_persists_and_reads_back() -> None:
    tenant = _tenant()
    try:
        cid = record_run_campaign(
            tenant, campaign_name="Angel Full-Day Special (run team-x)",
            artist="Angel", offer_type="full-day special", offer_price_usd=1200,
            message_copy="Book a full day with Angel...", cta="Reply ANGEL",
            recipient_count=10, delivered_count=8, failed_count=1, blocked_count=1,
            attachment_present=True, categories=["promo", "full-day"],
            location="Las Vegas", status="staged", run_id="team-x", dsn=DSN,
        )
        assert cid
        # Read back through a FRESH connection (restart proxy) — it persisted.
        rows = campaigns_for_artist(tenant, "Angel", dsn=DSN)
        assert len(rows) == 1
        r = rows[0]
        assert r["campaign_name"].startswith("Angel Full-Day Special")
        assert r["offer_price_usd"] == 1200
        assert r["cta"] == "Reply ANGEL"
        assert r["recipient_count"] == 10 and r["delivered_count"] == 8
        assert r["source"] == SOURCE_ENGINE_RUN
    finally:
        _teardown(tenant)


def test_record_is_idempotent_on_the_same_run() -> None:
    tenant = _tenant()
    try:
        for _ in range(2):
            record_run_campaign(
                tenant, campaign_name="Dup Campaign", artist="Bea",
                offer_type="flash", recipient_count=5, run_id="team-dup", dsn=DSN,
            )
        assert len(campaigns_for_artist(tenant, "Bea", dsn=DSN)) == 1
    finally:
        _teardown(tenant)


def test_last_campaign_returns_newest_for_artist() -> None:
    tenant = _tenant()
    try:
        record_run_campaign(tenant, campaign_name="Old", artist="Cyd",
                            offer_type="flash", sent_at="2026-01-01", run_id="r1", dsn=DSN)
        record_run_campaign(tenant, campaign_name="New", artist="Cyd",
                            offer_type="full-day", sent_at="2026-06-01", run_id="r2", dsn=DSN)
        last = last_campaign(tenant, "Cyd", dsn=DSN)
        assert last is not None and last["campaign_name"] == "New"
        # A different artist is isolated.
        assert last_campaign(tenant, "Nobody", dsn=DSN) is None
    finally:
        _teardown(tenant)


def test_summarize_last_is_a_grounded_reuse_string() -> None:
    tenant = _tenant()
    try:
        record_run_campaign(
            tenant, campaign_name="Angel $1200 Full-Day", artist="Angel",
            offer_type="full-day special", offer_price_usd=1200, cta="Reply ANGEL",
            recipient_count=994, delivered_count=980, failed_count=14,
            run_id="r1", dsn=DSN,
        )
        s = summarize_last(tenant, "Angel", dsn=DSN)
        assert s is not None
        assert "Angel" in s and "1200" in s and "Reply ANGEL" in s
        assert "994" in s
        # Honest-empty for an artist with no stored campaign.
        assert summarize_last(tenant, "Ghost", dsn=DSN) is None
    finally:
        _teardown(tenant)
