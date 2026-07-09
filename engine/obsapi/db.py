"""Postgres access for the obs-API (read model).

Reuses ``engine/autonomy/store.py``'s psycopg connection approach: a short-lived
``psycopg.connect(dsn, autocommit=True, row_factory=dict_row)`` per call. psycopg
is imported lazily inside :func:`connect` so ``import obsapi`` never requires the
driver or touches the DB at import time (the DoD ``python -c "import obsapi"``).

The DSN comes from ``ENGINE_DATABASE_URL`` (the same var the engine's durable
Postgres path reads), falling back to ``DATABASE_URL`` and finally the local
demo cluster.
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

DEFAULT_DSN = "postgresql://scalers:scalers@localhost:5432/scalers"


def get_dsn() -> str:
    """Return the Postgres DSN, preferring ``ENGINE_DATABASE_URL``."""

    return (
        os.environ.get("ENGINE_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
        or DEFAULT_DSN
    )


@contextmanager
def connect() -> Iterator[Any]:
    """Yield a short-lived autocommit connection with ``dict_row`` rows.

    psycopg is imported here (not at module load) so the package imports cleanly
    even in an environment without the driver wired.
    """

    import psycopg
    from psycopg.rows import dict_row

    conn = psycopg.connect(get_dsn(), autocommit=True, row_factory=dict_row)
    try:
        yield conn
    finally:
        conn.close()


def fetch_all(sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
    with connect() as conn:
        return conn.execute(sql, params).fetchall()


def fetch_one(sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
    with connect() as conn:
        return conn.execute(sql, params).fetchone()


def scalar(sql: str, params: Sequence[Any] = ()) -> Any:
    row = fetch_one(sql, params)
    if not row:
        return None
    return next(iter(row.values()))
