"""fr1.4 tenant redirect pins (AC-4) — PG integration.

The SDT tenant onboards in TEST mode with BOTH redirects pinned: no real
recipient reachable until a per-campaign operator flip (t90.4). Proves the pins
are active by default and that a live send for a pinned tenant is refused at the
connector boundary.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import psycopg
import pytest

from ops.redirects import (
    RedirectPinnedError,
    RedirectPins,
    assert_send_not_pinned_live,
    provision_sdt_tenant,
    tenant_redirect_pins,
)
from tenants import store as tenant_store

pytestmark = [
    pytest.mark.skipif(
        not os.environ.get("ENGINE_DATABASE_URL"),
        reason="requires Postgres (set ENGINE_DATABASE_URL)",
    ),
]

DSN = os.environ.get("ENGINE_DATABASE_URL", "postgresql://scalers:scalers@localhost:5432/scalers")
_INITDB = Path(__file__).resolve().parents[2] / "infra" / "initdb"


@pytest.fixture(scope="module", autouse=True)
def _schema():
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute((_INITDB / "12-tenants.sql").read_text(encoding="utf-8"))
        conn.execute((_INITDB / "18-tenant-isolation.sql").read_text(encoding="utf-8"))
    tenant_store.ensure_schema(DSN)


def _sdt() -> str:
    return f"sdt-{uuid.uuid4().hex[:8]}"


def test_sdt_provisioned_with_both_redirects_pinned():
    tid = _sdt()
    provision_sdt_tenant(tenant_id=tid, name="Skin Design", dsn=DSN)
    pins = tenant_redirect_pins(tid, dsn=DSN)
    assert pins == RedirectPins(sms=True, gmail=True)  # both active by default


def test_sdt_is_test_mode():
    tid = _sdt()
    provision_sdt_tenant(tenant_id=tid, dsn=DSN)
    row = tenant_store.get_tenant(tid, dsn=DSN)
    assert row["test_mode"] is True


def test_unprovisioned_tenant_is_unpinned():
    pins = tenant_redirect_pins(f"legacy-{uuid.uuid4().hex[:8]}", dsn=DSN)
    assert pins == RedirectPins(sms=False, gmail=False)


def test_live_send_refused_for_pinned_tenant():
    tid = _sdt()
    provision_sdt_tenant(tenant_id=tid, dsn=DSN)
    for channel in ("sms", "gmail"):
        with pytest.raises(RedirectPinnedError):
            assert_send_not_pinned_live(tid, channel, True, dsn=DSN)


def test_redirected_send_allowed_for_pinned_tenant():
    tid = _sdt()
    provision_sdt_tenant(tenant_id=tid, dsn=DSN)
    # requested_live=False is the sandbox path — never refused.
    assert_send_not_pinned_live(tid, "sms", False, dsn=DSN)  # no raise


def test_live_send_allowed_for_unpinned_tenant():
    assert_send_not_pinned_live(f"legacy-{uuid.uuid4().hex[:8]}", "sms", True, dsn=DSN)  # no raise


def test_sms_connector_blocks_live_send_for_pinned_tenant(monkeypatch):
    from tests.test_sms_connector import _FakeFetcher, _resolver
    from connectors.sms import SmsSendStatus, TwilioSmsConnector

    tid = _sdt()
    provision_sdt_tenant(tenant_id=tid, dsn=DSN)
    fake = _FakeFetcher()
    c = TwilioSmsConnector(
        enabled=True, account_sid="AC", auth_token="t", messaging_service_sid="MG",
        fetcher=fake, resolver=_resolver(),
    )
    result = c.send_sms(
        tenant_id=tid, to="+17025550123",
        body="SDT: book now. Reply STOP to opt out.", live=True, dsn=DSN,
    )
    assert result.status is SmsSendStatus.BLOCKED
    assert "pinned" in result.reason.lower()
    assert fake.calls == []  # never dispatched
