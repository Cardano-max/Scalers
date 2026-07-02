"""OPS-3 memory de-pollution (fr1.3, AC-5) — PG integration.

Audit ground truth: 102/362 ladies8391 memories were ``test_mem_*`` artifacts
injected top-5 into drafting context. This proves the mechanism that fixes it:
an ``is_test`` flag, a pattern backfill (flag — never delete), and a recall
default of ``is_test=false`` so a test artifact can never ground a real draft.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from suppression.ledger import (
    backfill_test_memories,
    get_memories,
    ingest_twilio_opt_out,
    record_consent,
    record_preference_memory,
)
from tests.conftest import private_schema

pytestmark = [
    pytest.mark.skipif(
        not os.environ.get("ENGINE_DATABASE_URL"),
        reason="requires Postgres (set ENGINE_DATABASE_URL)",
    ),
]

UTC = timezone.utc
NOW = datetime(2026, 7, 2, 19, 0, tzinfo=UTC)


def _schema():
    return private_schema("02-side-effect-boundary.sql", "14-suppression-consent.sql")


def test_recall_defaults_to_exclude_test_memories():
    with _schema() as s:
        record_preference_memory(
            tenant_id="t", identifier="+17025550001",
            content={"kind": "contact_preference", "cadence": "weekly"},
            valid_from=NOW - timedelta(days=10), dsn=s.dsn,
        )
        record_preference_memory(
            tenant_id="t", identifier="+17025550001",
            content={"kind": "contact_preference", "source": "test_mem_seed"},
            valid_from=NOW - timedelta(days=9), is_test=True, dsn=s.dsn,
        )
        recalled = get_memories(tenant_id="t", identifier="+17025550001", dsn=s.dsn)
        assert len(recalled) == 1
        assert all(not r["content"].get("source", "").startswith("test_mem_") for r in recalled)


def test_include_test_returns_everything():
    with _schema() as s:
        record_preference_memory(
            tenant_id="t", identifier="+17025550002",
            content={"kind": "contact_preference"}, valid_from=NOW, dsn=s.dsn,
        )
        record_preference_memory(
            tenant_id="t", identifier="+17025550002",
            content={"kind": "test"}, valid_from=NOW, is_test=True, dsn=s.dsn,
        )
        assert len(get_memories(tenant_id="t", identifier="+17025550002", dsn=s.dsn)) == 1
        assert len(
            get_memories(tenant_id="t", identifier="+17025550002", include_test=True, dsn=s.dsn)
        ) == 2


def test_backfill_flags_test_mem_pattern_never_deletes():
    with _schema() as s:
        # Two real rows + three test_mem_* artifacts (by source, by identifier, by kind).
        record_preference_memory(
            tenant_id="t", identifier="+17025550003",
            content={"kind": "contact_preference", "cadence": "weekly"},
            valid_from=NOW, dsn=s.dsn,
        )
        record_preference_memory(
            tenant_id="t", identifier="+17025550004",
            content={"kind": "contact_preference"}, valid_from=NOW, dsn=s.dsn,
        )
        record_preference_memory(
            tenant_id="t", identifier="+17025550003",
            content={"source": "test_mem_abc", "kind": "contact_preference"},
            valid_from=NOW, dsn=s.dsn,
        )
        record_preference_memory(
            tenant_id="t", identifier="test_mem_synthetic_lead",
            content={"kind": "contact_preference"}, valid_from=NOW, dsn=s.dsn,
        )
        record_preference_memory(
            tenant_id="t", identifier="+17025550005",
            content={"kind": "test"}, valid_from=NOW, dsn=s.dsn,
        )
        # Backfill (mislabeled real rows) — flags 3, leaves the 2 real ones.
        flagged = backfill_test_memories(tenant_id="t", dsn=s.dsn)
        assert flagged == 3
        # Total rows conserved (flag, not delete): 5 rows still present.
        import psycopg

        with psycopg.connect(s.dsn, autocommit=True) as c:
            total = c.execute("SELECT count(*) FROM contact_memories").fetchone()[0]
            flagged_n = c.execute(
                "SELECT count(*) FROM contact_memories WHERE is_test=true"
            ).fetchone()[0]
        assert total == 5
        assert flagged_n == 3


def test_backfill_is_idempotent():
    with _schema() as s:
        record_preference_memory(
            tenant_id="t", identifier="test_mem_x",
            content={"kind": "contact_preference"}, valid_from=NOW, dsn=s.dsn,
        )
        assert backfill_test_memories(tenant_id="t", dsn=s.dsn) == 1
        assert backfill_test_memories(tenant_id="t", dsn=s.dsn) == 0  # already flagged


def test_drafting_context_contains_zero_test_memories_after_backfill():
    with _schema() as s:
        for i in range(5):
            record_preference_memory(
                tenant_id="t", identifier="+17025550006",
                content={"source": f"test_mem_{i}", "kind": "contact_preference"},
                valid_from=NOW - timedelta(days=i), dsn=s.dsn,
            )
        record_preference_memory(
            tenant_id="t", identifier="+17025550006",
            content={"kind": "contact_preference", "cadence": "monthly"},
            valid_from=NOW, dsn=s.dsn,
        )
        backfill_test_memories(tenant_id="t", dsn=s.dsn)
        context = get_memories(tenant_id="t", identifier="+17025550006", dsn=s.dsn)
        assert len(context) == 1
        assert all("test_mem_" not in str(r["content"]) for r in context)


def test_stop_supersede_on_test_contact_inherits_is_test():
    # A STOP superseding a TEST contact's open memory must not inject a REAL
    # do-not-contact row into recall — the supersede inherits is_test.
    with _schema() as s:
        record_consent(
            tenant_id="t", identifier="+17025550007", channel="sms", source="web_form",
            granted_at=NOW - timedelta(days=30), dsn=s.dsn,
        )
        record_preference_memory(
            tenant_id="t", identifier="+17025550007",
            content={"source": "test_mem_seed", "kind": "contact_preference"},
            valid_from=NOW - timedelta(days=20), is_test=True, dsn=s.dsn,
        )
        ingest_twilio_opt_out(
            {"OptOutType": "STOP", "From": "+17025550007", "Body": "STOP"},
            tenant_id="t", occurred_at=NOW, dsn=s.dsn,
        )
        # Default recall (real only) sees nothing from this test contact.
        assert get_memories(tenant_id="t", identifier="+17025550007", dsn=s.dsn) == []
        # The supersede row exists but is flagged is_test.
        allrows = get_memories(
            tenant_id="t", identifier="+17025550007", include_test=True, dsn=s.dsn
        )
        assert any(r["content"].get("do_not_contact") for r in allrows)


def test_stop_supersede_on_real_contact_is_real():
    with _schema() as s:
        record_consent(
            tenant_id="t", identifier="+17025550008", channel="sms", source="web_form",
            granted_at=NOW - timedelta(days=30), dsn=s.dsn,
        )
        record_preference_memory(
            tenant_id="t", identifier="+17025550008",
            content={"kind": "contact_preference", "cadence": "weekly"},
            valid_from=NOW - timedelta(days=20), dsn=s.dsn,
        )
        ingest_twilio_opt_out(
            {"OptOutType": "STOP", "From": "+17025550008", "Body": "STOP"},
            tenant_id="t", occurred_at=NOW, dsn=s.dsn,
        )
        rows = get_memories(tenant_id="t", identifier="+17025550008", dsn=s.dsn)
        assert any(r["content"].get("do_not_contact") for r in rows)  # visible in real recall
