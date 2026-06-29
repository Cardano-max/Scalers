#!/usr/bin/env python3
"""done-gate — the single "done means green" check for the Scalers monorepo.

Runs every required quality gate and exits non-zero if any of them fail. It is
deliberately cross-platform (pure Python, no bash-isms) so it runs identically
on a Windows dev box and on Linux CI.

Gates, in order:
  1. engine: ruff lint            (ruff check)
  2. engine: format check         (ruff format --check)  [Black-compatible]
  3. engine: tests (+coverage)    (pytest, with coverage when pytest-cov present)
  4. frontend: lint               (npm run lint in gateway/ and web/)
  5. evals: eval gate             (promptfoo, opt-in)

Steps gracefully SKIP (not fail) when their subproject is not scaffolded yet —
e.g. gateway/web have no package.json in Phase 1, and the eval gate is opt-in.
A skipped step never turns the gate red; only a real failure does.

Usage:
  python scripts/done_gate.py            # run all gates
  python scripts/done_gate.py --python-only
  python scripts/done_gate.py --no-coverage
  EVAL_GATE=1 python scripts/done_gate.py   # also enforce the eval gate
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ENGINE_DIR = REPO_ROOT / "engine"

# ANSI is optional; CI logs render it fine and plain terminals ignore unknowns.
PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"
WARN = "WARN"  # advisory: printed, but never fails the gate


class Result:
    def __init__(self, name: str, status: str, detail: str = "") -> None:
        self.name = name
        self.status = status
        self.detail = detail


def _have(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def _engine_argv(tool_argv: list[str]) -> list[str]:
    """Wrap a tool invocation in ``uv run`` when uv is available.

    The engine is a uv project (``[tool.uv] package = false``; deps + dev tools
    live in ``uv.lock``), so the gate runs its tools through ``uv run`` to use the
    synced environment. Falls back to a bare invocation if uv is not installed.
    """
    if _have("uv"):
        return ["uv", "run", *tool_argv]
    return tool_argv


def _run(name: str, argv: list[str], cwd: Path) -> Result:
    """Run a command, streaming output, and map its exit code to PASS/FAIL."""
    print(f"\n::: {name}: $ {' '.join(argv)}  (cwd={cwd.relative_to(REPO_ROOT)})", flush=True)
    try:
        proc = subprocess.run(argv, cwd=str(cwd))
    except FileNotFoundError:
        return Result(name, FAIL, f"command not found: {argv[0]}")
    status = PASS if proc.returncode == 0 else FAIL
    return Result(name, status, "" if status == PASS else f"exit {proc.returncode}")


def gate_engine_lint() -> Result:
    """Hard gate: ruff lint (default rule set) on the engine."""
    return _run("engine:ruff-lint", _engine_argv(["ruff", "check", "."]), ENGINE_DIR)


def gate_engine_format() -> Result:
    """Advisory: ruff format check. Reported but NEVER fails the gate.

    `ruff format` adoption is a documented follow-up (it would reformat in-flight
    Phase-1 code), so a format drift is a WARN, not a FAIL.
    """
    res = _run("engine:format", _engine_argv(["ruff", "format", "--check", "."]), ENGINE_DIR)
    if res.status == FAIL:
        return Result("engine:format", WARN, "format drift (advisory; `ruff format` adoption pending)")
    return res


def gate_engine_tests(use_coverage: bool) -> Result:
    """Hard gate: unit tests with coverage. Integration tests (real Postgres) are
    EXCLUDED here — the local gate is DB-free; CI runs them in a pgvector job."""
    argv = ["pytest", "-m", "not integration"]
    if use_coverage:
        # `--cov` with no package uses [tool.coverage.run] source in pyproject.toml.
        argv += ["--cov", "--cov-report=term-missing", "--cov-fail-under=50"]
    return _run("engine:pytest", _engine_argv(argv), ENGINE_DIR)


def gate_frontend(subdir: str) -> Result:
    name = f"frontend:{subdir}"
    pkg = REPO_ROOT / subdir / "package.json"
    if not pkg.exists():
        return Result(name, SKIP, "no package.json yet (eslint/prettier land when the app is scaffolded)")
    if not (REPO_ROOT / subdir / "node_modules").exists():
        # deps not installed in this job (e.g. the engine done-gate job does not
        # npm-install the apps); the dedicated `frontend (node)` CI job runs the
        # real lint with deps installed. SKIP here rather than exit 127.
        return Result(name, SKIP, "node_modules not installed here; dedicated frontend CI job runs the lint")
    if not _have("npm"):
        return Result(name, FAIL, "npm not installed but package.json present")
    return _run(name, ["npm", "run", "lint", "--if-present"], REPO_ROOT / subdir)


def gate_evals() -> Result:
    """Per-commit eval gate (ADR Decision 4) at the EVAL_GATE seam.

    Opt-in (`EVAL_GATE=1`) and KB-gated: runs the registered PER_COMMIT metric
    gates over the eval KB and fails on a regressed required metric. SKIP (neutral)
    when not opted in or no KB is reachable — never a false build failure.
    """
    name = "evals:gate"
    if os.environ.get("EVAL_GATE") != "1":
        return Result(name, SKIP, "opt-in (set EVAL_GATE=1 to enforce)")
    if not os.environ.get("ENGINE_DATABASE_URL"):
        return Result(name, SKIP, "no ENGINE_DATABASE_URL (eval KB unavailable)")
    return _run(name, _engine_argv(["python", "-m", "evals.run_gate"]), ENGINE_DIR)


def gate_skill_registry() -> Result:
    """Hard gate: skill-registry consistency (1mk.10 / sec supply-chain guardrail).

    Enforces the 1mk.1 HARD RULE — no registry row -> no skill use — plus
    provenance (a vendored skill's pin must match the registry pin). Pure-Python,
    no deps, so it runs everywhere the done-gate does.
    """
    script = REPO_ROOT / "scripts" / "check_skill_registry.py"
    if not script.exists():
        return Result("skill-registry", SKIP, "checker not present")
    return _run("skill-registry", [sys.executable, str(script)], REPO_ROOT)


def main() -> int:
    parser = argparse.ArgumentParser(description="Scalers done-gate")
    parser.add_argument("--python-only", action="store_true", help="run only engine gates")
    parser.add_argument("--no-coverage", action="store_true", help="skip the coverage threshold")
    args = parser.parse_args()

    results: list[Result] = [
        gate_engine_lint(),
        gate_engine_format(),
        gate_engine_tests(use_coverage=not args.no_coverage),
        gate_skill_registry(),
    ]
    if not args.python_only:
        results += [
            gate_frontend("gateway"),
            gate_frontend("web"),
            gate_evals(),
        ]

    print("\n" + "=" * 60)
    print("done-gate summary")
    print("=" * 60)
    for r in results:
        line = f"  [{r.status:<4}] {r.name}"
        if r.detail:
            line += f"  - {r.detail}"
        print(line)

    failed = [r for r in results if r.status == FAIL]
    print("=" * 60)
    if failed:
        print(f"DONE-GATE: FAIL ({len(failed)} failing)")
        return 1
    print("DONE-GATE: PASS (green)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
