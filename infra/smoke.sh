#!/usr/bin/env bash
# Scalers local-stack smoke test — verifies every INFRA-01 acceptance criterion.
# Usage:  bash infra/smoke.sh        (run from repo root or infra/)
# Assumes the stack is already up:  docker compose up -d
set -euo pipefail

cd "$(dirname "$0")"

# Load .env if present so port/credential overrides are honored.
if [ -f .env ]; then set -a; . ./.env; set +a; fi

POSTGRES_USER="${POSTGRES_USER:-scalers}"
POSTGRES_DB="${POSTGRES_DB:-scalers}"
MINIO_CONSOLE_PORT="${MINIO_CONSOLE_PORT:-9001}"
MINIO_BUCKET="${MINIO_BUCKET:-scalers-assets}"

pass=0; fail=0
ok()   { echo "  PASS  $1"; pass=$((pass+1)); }
bad()  { echo "  FAIL  $1"; fail=$((fail+1)); }

echo "== Scalers stack smoke test =="

# 1. All services report healthy (minio-init is a one-shot, exits 0).
echo "[1] container health"
for svc in scalers-postgres scalers-redis scalers-minio; do
  status="$(docker inspect -f '{{.State.Health.Status}}' "$svc" 2>/dev/null || echo missing)"
  [ "$status" = "healthy" ] && ok "$svc healthy" || bad "$svc status=$status"
done

# 2. pgvector available in psql.
echo "[2] pgvector"
ver="$(docker exec scalers-postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc \
  "SELECT extversion FROM pg_extension WHERE extname='vector'" 2>/dev/null || true)"
[ -n "$ver" ] && ok "pgvector extension present (v$ver)" || bad "pgvector extension missing"

# 3. Redis PING.
echo "[3] redis"
[ "$(docker exec scalers-redis redis-cli ping 2>/dev/null)" = "PONG" ] \
  && ok "redis PING -> PONG" || bad "redis did not PONG"

# 4. MinIO console reachable (HTTP responds on the console port).
echo "[4] minio console"
code="$(curl -s -o /dev/null -w '%{http_code}' "http://localhost:${MINIO_CONSOLE_PORT}" || echo 000)"
# MinIO console redirects (3xx) or serves the login page (200) — anything non-000 means it's up.
[ "$code" != "000" ] && ok "console HTTP ${code} on :${MINIO_CONSOLE_PORT}" \
  || bad "console unreachable on :${MINIO_CONSOLE_PORT}"

# 5. Assets bucket was bootstrapped.
echo "[5] minio bucket"
docker exec scalers-minio mc alias set chk http://localhost:9000 \
  "${MINIO_ROOT_USER:-scalers}" "${MINIO_ROOT_PASSWORD:-scalers-dev-secret}" >/dev/null 2>&1 || true
docker exec scalers-minio mc ls "chk/${MINIO_BUCKET}" >/dev/null 2>&1 \
  && ok "bucket '${MINIO_BUCKET}' exists" || bad "bucket '${MINIO_BUCKET}' missing"

echo
echo "== ${pass} passed, ${fail} failed =="
[ "$fail" -eq 0 ]
