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

Also runs the rvy.8 REAL calibration gate (D2-as-amended, evals/calibration.py)
additively after the registry gates: exit 1 if EITHER gate FAILs; both messages
are printed. ``CALIBRATION_GATE=0`` disables it (default on). Today the
calibration lane SKIPs (neutral): the real jury quality (4jx.2 aggregate) is not
recorded for gold examples in the eval lane yet and fabricating confidence is
forbidden — blocking flips live the moment real pairs flow.
"""

from __future__ import annotations

import os

from autonomy.confidence import PROVENANCE_COMPUTED
from evals.calibration import deterministic_probe_confidence_fn, run_calibration_gate
from evals.predictors import cell_predictor, regressed_cell_predictor
from evals.gate import run_eval_gate
from evals.smoke_gold_set import SMOKE_TENANT, load_smoke_gold_set
from kb.schema import Engine, RunKind


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
    predictor = regressed_cell_predictor if regress else cell_predictor
    result = run_eval_gate(
        store, predictor, tenant_id=SMOKE_TENANT,
        run_kind=RunKind.PER_COMMIT, git_sha=os.environ.get("GIT_SHA"),
    )
    print(result.message())
    failed = result.verdict == "FAIL"

    # rvy.8: REAL calibration gate (additive; CALIBRATION_GATE=0 disables).
    # The confidence source is the real 4jx.3 pipeline (K deterministic predictor
    # probes -> self-consistency, pooled with jury quality). No real jury quality
    # is recorded for gold examples in the eval lane yet (4jx.2 integration
    # pending) and fabricating one is forbidden, so jury_quality_source stays
    # None -> zero pairs -> SKIP (neutral). A FAIL here reds the build.
    if os.environ.get("CALIBRATION_GATE", "1") != "0":
        cal_result = run_calibration_gate(
            store,
            tenant_id=SMOKE_TENANT,
            engine=Engine.ENGAGEMENT,
            cell="triage",
            dimension="triage_class",
            predictor=predictor,
            confidence_fn=deterministic_probe_confidence_fn(predictor, jury_quality_source=None),
            git_sha=os.environ.get("GIT_SHA"),
            # 4jx.17 AC2: the probe fn runs the REAL compute_confidence pipeline,
            # so its rows are tagged with the computed producer.
            confidence_provenance=PROVENANCE_COMPUTED,
        )
        print(cal_result.message())
        failed = failed or cal_result.verdict == "FAIL"

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
