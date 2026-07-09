"""PG-integration test for the follow-up detector's opt-out resolution against the
REAL t90.3 suppression ledger (CustomerAcq-fr1.1 AC-8).

Proves the AC-8 opt-out exclusion end-to-end: a recipient with a recorded STOP is
resolved as opted-out from the suppression ledger and dropped from the follow-up set.

Requires a real local Postgres (RUN_PG_TESTS / ENGINE_DATABASE_URL).
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone

import pytest

from tests.conftest import private_schema

pytestmark = pytest.mark.skipif(
    not os.getenv("ENGINE_DATABASE_URL"), reason="requires Postgres"
)

TENANT = "sdt-test"


def test_opted_out_recipient_excluded_via_real_suppression_ledger():
    from proactive.detectors import PriorSend, follow_up_opportunities
    from proactive.followup_source import resolve_opted_out
    from suppression.ledger import record_suppression

    with private_schema(
        "02-side-effect-boundary.sql", "16-suppression-consent.sql"
    ) as sch:
        # a@x.com said STOP; b@x.com stayed opted in.
        record_suppression(
            tenant_id=TENANT, identifier="a@x.com", channel="all",
            reason="stop", occurred_at=datetime(2026, 7, 9, tzinfo=timezone.utc),
            dsn=sch.dsn,
        )

        today = date(2026, 7, 10)
        sends = [
            PriorSend("a@x.com", today - timedelta(days=3), "camp1", spots_remaining=2),
            PriorSend("b@x.com", today - timedelta(days=3), "camp1", spots_remaining=2),
        ]
        recipients = [s.recipient for s in sends]

        opted_out = resolve_opted_out(
            tenant_id=TENANT, identifiers=recipients, channel="email", dsn=sch.dsn
        )
        assert opted_out == frozenset({"a@x.com"})  # resolved from the real ledger

        opps = follow_up_opportunities(today, prior_sends=sends, opted_out=opted_out)
        assert {o.facts["recipient"] for o in opps} == {"b@x.com"}
