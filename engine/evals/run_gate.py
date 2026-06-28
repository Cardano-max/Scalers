"""Per-commit eval-gate CLI (rvy.9) — the seam the done-gate calls (ADR Decision 4).

Runs the registered PER_COMMIT gates over the KB's gold set and exits non-zero if a
required metric regressed, writing each result to ``eval_metric``. Until rvy.7's
Inspect mock-model solvers land, the "cell under test" is a deterministic demo
predictor over the rvy.10 smoke set — proving the build-fail wiring now.

Usage (run inside engine/, with the KB reachable):
    ENGINE_DATABASE_URL=... uv run python -m evals.run_gate            # clean -> exit 0 (GREEN)
    ENGINE_DATABASE_URL=... EVAL_GATE_REGRESS=1 uv run python -m evals.run_gate  # seeded regression -> exit 1 (RED)

No ENGINE_DATABASE_URL -> SKIP (neutral, exit 0): the gate never false-fails when
the KB is unavailable, mirroring the done-gate's graceful-skip discipline.
"""

from __future__ import annotations

import os

from evals.demo_cells import oracle_cell, regressed_triage_cell
from evals.gate import run_eval_gate
from evals.smoke_gold_set import SMOKE_TENANT, load_smoke_gold_set
from kb.schema import RunKind


def main() -> int:
    dsn = os.environ.get("ENGINE_DATABASE_URL")
    if not dsn:
        print("eval-gate SKIP - no ENGINE_DATABASE_URL (KB unavailable)")
        return 0

    from kb.store import KbStore

    store = KbStore(dsn)
    # Until rvy.7 lands real per-commit fixtures, exercise the gate on the smoke
    # set (idempotent load). The predictor is the seam rvy.7's solver swaps into.
    load_smoke_gold_set(store)

    regress = os.environ.get("EVAL_GATE_REGRESS") == "1"
    predictor = regressed_triage_cell if regress else oracle_cell
    result = run_eval_gate(
        store, predictor, tenant_id=SMOKE_TENANT,
        run_kind=RunKind.PER_COMMIT, git_sha=os.environ.get("GIT_SHA"),
    )
    print(result.message())
    return 1 if result.verdict == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
