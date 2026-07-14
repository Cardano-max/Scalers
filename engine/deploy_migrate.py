"""Container-friendly schema bootstrap: apply infra/initdb/*.sql + runtime stores.

The local dev path applies ``infra/initdb/*.sql`` via Docker's initdb (fresh
volume) or ``infra/migrate.sh`` (docker exec + psql). Neither exists inside a
cloud container (Render/Railway/Fly), so this runner does the same work with
psycopg alone:

    ENGINE_DATABASE_URL=postgres://... python deploy_migrate.py

* Applies every ``infra/initdb/*.sql`` in filename order. All migrations are
  idempotent (CREATE ... IF NOT EXISTS), so re-running on every boot is safe —
  that is exactly how the Docker entrypoint uses it.
* A file that fails because the managed Postgres lacks an optional extension
  (pgvector on a plan that doesn't allow it) is reported and SKIPPED — the
  vector-dependent stores then degrade the same way they do in local dev
  without pgvector — unless STRICT_MIGRATIONS=1, in which case any failure
  exits non-zero (recommended once the target DB is known-good).
* Finishes with ``bootstrap_db.main()`` so every runtime-created store exists
  before the first request, not mid-request.

Looks for the SQL in ``../infra/initdb`` (repo layout) and ``./infra/initdb``
(the Docker image copies it next to the engine).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _initdb_dir() -> Path | None:
    here = Path(__file__).resolve().parent
    for cand in (here.parent / "infra" / "initdb", here / "infra" / "initdb"):
        if cand.is_dir():
            return cand
    return None


def main() -> int:
    dsn = os.environ.get("ENGINE_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        print("deploy_migrate: no ENGINE_DATABASE_URL / DATABASE_URL — nothing to do")
        return 2
    # Some managed hosts hand out postgres:// which psycopg accepts; normalize the
    # sqlalchemy-style prefix a few dashboards emit.
    if dsn.startswith("postgresql+psycopg://"):
        dsn = "postgresql://" + dsn.removeprefix("postgresql+psycopg://")

    import psycopg

    strict = os.environ.get("STRICT_MIGRATIONS", "") in ("1", "true", "yes")
    initdb = _initdb_dir()
    failures: list[str] = []
    if initdb is None:
        print("deploy_migrate: no infra/initdb directory found — skipping SQL chain")
    else:
        for sql_file in sorted(initdb.glob("*.sql")):
            sql = sql_file.read_text(encoding="utf-8")
            try:
                with psycopg.connect(dsn, autocommit=True) as conn:
                    conn.execute(sql)
                print(f"  applied {sql_file.name}")
            except Exception as exc:  # noqa: BLE001 — report + continue/abort below
                failures.append(f"{sql_file.name}: {type(exc).__name__}: {exc}")
                print(f"  FAILED  {sql_file.name}: {exc}")
                if strict:
                    print("deploy_migrate: STRICT_MIGRATIONS=1 — aborting")
                    return 1

    # Runtime-created stores (23 CREATE-IF-NOT-EXISTS bootstraps + lazy ALTERs).
    os.environ.setdefault("ENGINE_DATABASE_URL", dsn)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import bootstrap_db

    rc = bootstrap_db.main()
    if failures:
        print(f"deploy_migrate: done with {len(failures)} skipped file(s) "
              f"(set STRICT_MIGRATIONS=1 to fail on these): {failures}")
    else:
        print("deploy_migrate: all migrations + store bootstraps applied")
    return rc if isinstance(rc, int) else 0


if __name__ == "__main__":
    raise SystemExit(main())
