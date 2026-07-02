"""NOSUPERUSER runtime DB guard (CustomerAcq-fr1.4, AC-1).

The audit found the engine connecting to Postgres as a SUPERUSER — which
*bypasses* Row-Level Security, defeating the tenant-isolation backstop. The
production runtime must connect as the NOSUPERUSER ``scalers_app`` role so RLS
actually bites. This guard is the boot-time assertion: in production it refuses
a superuser connection outright, so a misconfigured DSN fails loud at startup
instead of silently running with RLS disabled.

In dev/test the guard is permissive (it only warns) — the suites connect as the
superuser ``scalers`` role by design, and RLS is exercised explicitly by
connecting as ``scalers_app`` where it matters.
"""

from __future__ import annotations

import logging
import os

import psycopg

__all__ = [
    "SuperuserInProdError",
    "assert_runtime_role_ok",
    "connection_is_superuser",
    "is_prod",
]

log = logging.getLogger(__name__)

_DEFAULT_DSN = "postgresql://scalers:scalers@localhost:5432/scalers"


class SuperuserInProdError(RuntimeError):
    """The production runtime resolved a SUPERUSER database connection, which
    bypasses Row-Level Security. Refused at boot — configure ``scalers_app``."""


def is_prod(prod: bool | None = None) -> bool:
    """Whether we are in production mode. Explicit ``prod`` wins; otherwise read
    ``SCALERS_ENV`` / ``APP_ENV`` (``production`` / ``prod``)."""
    if prod is not None:
        return prod
    env = (os.environ.get("SCALERS_ENV") or os.environ.get("APP_ENV") or "").strip().lower()
    return env in ("production", "prod")


def connection_is_superuser(conn: psycopg.Connection) -> bool:
    """True iff the connection's role has the SUPERUSER attribute (RLS bypass)."""
    row = conn.execute("SELECT current_setting('is_superuser')").fetchone()
    val = row[0] if not isinstance(row, dict) else next(iter(row.values()))
    return str(val).strip().lower() == "on"


def assert_runtime_role_ok(dsn: str | None = None, *, prod: bool | None = None) -> bool:
    """Assert the runtime DB role is acceptable and return whether it is a
    superuser. In production a superuser connection raises
    :class:`SuperuserInProdError`; in dev it only logs a warning. Opens one short
    connection to ask the server (never trusts the DSN string)."""
    resolved = dsn or os.environ.get("ENGINE_DATABASE_URL") or _DEFAULT_DSN
    with psycopg.connect(resolved, connect_timeout=5) as conn:
        superuser = connection_is_superuser(conn)
    if superuser:
        if is_prod(prod):
            raise SuperuserInProdError(
                "runtime DB connection is a SUPERUSER (bypasses RLS) — production "
                "must connect as the NOSUPERUSER scalers_app role. Refusing to boot."
            )
        log.warning(
            "runtime DB connection is a SUPERUSER — RLS is bypassed. Acceptable in "
            "dev/test; production requires the scalers_app role."
        )
    return superuser
