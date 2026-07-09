#!/usr/bin/env bash
# One-shot local bring-up for a fresh clone (Linux/macOS/Git-Bash).
#
# Order matters and mirrors what the engine actually needs:
#   1. Postgres reachable at $SCALERS_PG (docker compose OR a native cluster)
#   2. infra/initdb/*.sql applied (idempotent)
#   3. engine/bootstrap_db.py — provisions every lazily-created store table
#      (agent_runs, runs, autonomy_decisions, ...) so an early-crashing run
#      can't leave the DB half-provisioned
#   4. engine on :8000, console on :3000
#
# Credentials: put the operator .env at engine/.env (gitignored). Without an
# ANTHROPIC_API_KEY the pipeline degrades to deterministic drafts (honest,
# not fabricated); without FIRECRAWL_API_KEY research returns empty.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PG_DSN="${SCALERS_PG:-postgresql://scalers:scalers@localhost:5432/scalers}"

echo "== 1/4 Postgres"
if ! psql "$PG_DSN" -c "SELECT 1" >/dev/null 2>&1; then
  echo "   Postgres not reachable at $PG_DSN — trying docker compose ..."
  (cd "$ROOT/infra" && docker compose up -d postgres redis 2>/dev/null) || {
    echo "   docker unavailable; start Postgres manually (see LOCAL-RUN-GUIDE.md)"; exit 1; }
  for _ in $(seq 1 30); do psql "$PG_DSN" -c "SELECT 1" >/dev/null 2>&1 && break; sleep 2; done
fi
psql "$PG_DSN" -c "SELECT 1" >/dev/null

echo "== 2/4 Schema (infra/initdb, idempotent)"
for f in "$ROOT"/infra/initdb/*.sql; do
  psql "$PG_DSN" -q -f "$f" >/dev/null 2>&1 || true
done

echo "== 3/4 Store bootstrap (engine/bootstrap_db.py)"
cd "$ROOT/engine"
command -v uv >/dev/null || pip install uv >/dev/null
uv sync --extra postgres >/dev/null
ENGINE_DATABASE_URL="$PG_DSN" SCALERS_EMBEDDER="${SCALERS_EMBEDDER:-deterministic}" \
  uv run python bootstrap_db.py

echo "== 4/4 Engine :8000 + console :3000"
ENGINE_DATABASE_URL="$PG_DSN" SCALERS_EMBEDDER="${SCALERS_EMBEDDER:-deterministic}" \
  STUDIO_TENANT_ID="${STUDIO_TENANT_ID:-skindesign}" \
  nohup uv run uvicorn main:app --host 127.0.0.1 --port 8000 > /tmp/scalers-engine.log 2>&1 &
cd "$ROOT/web"
[ -f .env.local ] || sed "s/ladies8391/${STUDIO_TENANT_ID:-skindesign}/; s|http://127.0.0.1:8010|http://127.0.0.1:8000|" .env.example > .env.local
npm install >/dev/null 2>&1
nohup npm run dev -- --port 3000 > /tmp/scalers-web.log 2>&1 &

sleep 10
curl -sf http://localhost:8000/healthz >/dev/null && echo "engine  :8000 OK" || echo "engine  :8000 NOT UP (see /tmp/scalers-engine.log)"
curl -sf http://localhost:3000 -o /dev/null && echo "console :3000 OK" || echo "console :3000 NOT UP (see /tmp/scalers-web.log)"
echo "Open http://localhost:3000"
