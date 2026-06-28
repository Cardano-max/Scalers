#!/usr/bin/env bash
# Apply all schema migrations to a RUNNING Postgres container.
#
# Fresh clusters get these automatically (initdb runs on first boot). Use this
# when the data volume already exists and you've pulled a new migration:
#   bash infra/migrate.sh
#
# All migrations are idempotent (CREATE ... IF NOT EXISTS), so re-running is safe.
set -euo pipefail
cd "$(dirname "$0")"

if [ -f .env ]; then set -a; . ./.env; set +a; fi
PG_USER="${POSTGRES_USER:-scalers}"
PG_DB="${POSTGRES_DB:-scalers}"
CONTAINER="${PG_CONTAINER:-scalers-postgres}"

echo "Applying migrations to ${CONTAINER} (${PG_DB})..."
for f in initdb/*.sql; do
  echo "  -> $f"
  docker exec -i "$CONTAINER" psql -v ON_ERROR_STOP=1 -U "$PG_USER" -d "$PG_DB" < "$f"
done
echo "Migrations applied."
