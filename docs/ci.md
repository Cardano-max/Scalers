# Test / CI scaffold

Phase-1 quality gate for the Scalers monorepo. **Done means green:** every change
to `main` is gated by lint + tests before it can merge.

## TL;DR — run the gate locally

The engine is a [uv](https://docs.astral.sh/uv/) project; the gate runs its tools
through `uv run`.

```bash
# one-time: sync the engine env (runtime + dev tools, from uv.lock)
cd engine && uv sync --dev && cd ..

# run the full done-gate (what CI runs, minus the real-Postgres job)
python scripts/done_gate.py
```

A green run prints `DONE-GATE: PASS (green)` and exits `0`. Any real failure
prints `DONE-GATE: FAIL` and exits non-zero.

## What the gate checks

| # | Gate | Tool | Hard/advisory |
|---|------|------|---------------|
| 1 | engine lint | `ruff check` (default rules) | **hard** |
| 2 | engine format | `ruff format --check` | advisory (WARN) |
| 3 | engine unit tests (+coverage) | `pytest -m "not integration"` | **hard** |
| 4 | frontend lint | `npm run lint` | hard when scaffolded, else skip |
| 5 | eval gate (opt-in) | `promptfoo` | opt-in (`EVAL_GATE=1`) |

Integration tests against a **real Postgres** run only in CI (see below), not in
the local done-gate.

**Lint baseline.** Lint uses ruff's default rule set (pyflakes + pycodestyle
E4/E7/E9), which the existing engine already satisfies. Stricter rules (isort,
pyupgrade, bugbear) and a **hard** `ruff format --check` are a documented
follow-up the team opts into — adopting them now would force a large reformat
diff across the in-flight Phase-1 branches, so format drift is reported as an
advisory WARN rather than failing the gate.

### Graceful skips (not failures)

The gate is monorepo- and multi-language-aware. A step **skips** (stays green)
rather than failing when its subproject isn't scaffolded yet:

- **frontend** — `gateway/` and `web/` have no `package.json` in Phase 1, so the
  eslint/prettier step is a documented placeholder until those apps land.
- **evals** — the eval gate is opt-in (`EVAL_GATE=1`) so Phase-1 CI stays green
  without model keys. The real gold set + assertions arrive in Phase 2.

### Coverage threshold — STUB

`--cov-fail-under=50` is a floor, not a target. Phase 1 has almost no runtime
code; raise it in `engine/pyproject.toml` (`[tool.coverage.report] fail_under`)
and in CI as the harness lands in Phase 2+.

## Done-gate options

```bash
python scripts/done_gate.py --python-only   # engine gates only
python scripts/done_gate.py --no-coverage   # skip the coverage threshold
EVAL_GATE=1 python scripts/done_gate.py      # also enforce the eval gate
```

## CI (GitHub Actions)

`.github/workflows/ci.yml` runs on **pull requests to `main`** (and pushes to
`main`). Four jobs:

- **engine · lint + unit tests** — `astral-sh/setup-uv` (cached on `uv.lock`) →
  `uv sync --dev` → `uv run ruff check` + advisory format check + `uv run pytest
  -m "not integration"` with coverage.
- **engine · integration (real Postgres)** — stands up a **pgvector** service
  container, sets `ENGINE_DATABASE_URL`, `uv sync --dev --extra postgres`, and
  runs `uv run pytest -m integration`. This is what stops real-PG /
  async-checkpointer defects from hiding under `InMemorySaver` — the gated
  integration tests actually execute against a live database in CI.
- **frontend (node)** — matrix over `gateway` / `web`, setup-node 20 with npm
  cache; runs `npm ci && npm run lint` when a `package.json` exists, else skips.
- **done-gate** — needs the three above; re-runs `scripts/done_gate.py` so the
  authoritative DB-free "green" check is identical to the one developers run locally.

Caching: uv cache keyed on `engine/uv.lock`, npm keyed on each app's
`package-lock.json`. Superseded runs on the same ref are auto-cancelled.

### Integration tests (the `integration` marker)

Any test that needs a live Postgres carries `@pytest.mark.integration` (or
`pytestmark = pytest.mark.integration`) and reads `ENGINE_DATABASE_URL`. The unit
job and local done-gate run `-m "not integration"` (DB-free); the integration job
runs `-m integration` against the pgvector service. The engine checkpointer +
run-store integration tests (HARN-03) join this job by carrying the same marker.

## Observability — Langfuse (self-hosted)

Traces + evals run on self-hosted Langfuse v3 (ClickHouse-backed).

```bash
cp infra/.env.example infra/.env     # fill in secrets
docker compose --env-file infra/.env -f infra/docker-compose.langfuse.yml up -d
# open http://localhost:3000, create a project, paste its keys into infra/.env
```

The engine reads `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST`
via `observability.get_langfuse()` (`engine/observability.py`), which returns
`None` (no-op) when unconfigured — so tests and CI never need a live server.

## Eval gating (promptfoo / DeepEval)

`evals/promptfooconfig.yaml` is a non-blocking stub wired into the done-gate
behind `EVAL_GATE=1`. Phase 2 replaces the echo provider with the real engine
cell and points `tests` at the gold set, then the gate becomes mandatory.
