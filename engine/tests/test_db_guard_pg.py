"""fr1.4 NOSUPERUSER runtime boot guard (AC-1) — PG integration.

Production must connect as the NOSUPERUSER ``scalers_app`` role so RLS is not
bypassed. The guard refuses a superuser connection in prod mode and is
permissive in dev.
"""

from __future__ import annotations

import os

import pytest

from ops.db_guard import (
    SuperuserInProdError,
    assert_runtime_role_ok,
    connection_is_superuser,
    is_prod,
)
from ops.tenancy import app_role_dsn

pytestmark = [
    pytest.mark.skipif(
        not os.environ.get("ENGINE_DATABASE_URL"),
        reason="requires Postgres (set ENGINE_DATABASE_URL)",
    ),
]

DSN = os.environ.get("ENGINE_DATABASE_URL", "postgresql://scalers:scalers@localhost:5432/scalers")


def test_is_prod_reads_env(monkeypatch):
    monkeypatch.delenv("SCALERS_ENV", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    assert is_prod() is False
    monkeypatch.setenv("SCALERS_ENV", "production")
    assert is_prod() is True
    assert is_prod(False) is False  # explicit wins


def test_superuser_connection_detected():
    import psycopg

    with psycopg.connect(DSN) as conn:
        assert connection_is_superuser(conn) is True  # the default 'scalers' role


def test_superuser_refused_in_prod():
    with pytest.raises(SuperuserInProdError):
        assert_runtime_role_ok(DSN, prod=True)


def test_superuser_allowed_in_dev():
    # Dev is permissive (warns) — returns True (is superuser), does not raise.
    assert assert_runtime_role_ok(DSN, prod=False) is True


def test_app_role_passes_prod_guard():
    import psycopg

    app_dsn = app_role_dsn(DSN)
    try:
        with psycopg.connect(app_dsn) as conn:
            assert connection_is_superuser(conn) is False
    except psycopg.OperationalError as exc:
        pytest.skip(f"scalers_app role not available ({exc})")
    # A NOSUPERUSER connection is fine even in prod mode.
    assert assert_runtime_role_ok(app_dsn, prod=True) is False
