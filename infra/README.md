# infra — local development stack

One command brings up the durable substrate the engine depends on. No cloud, no
AWS — everything runs in local Docker on named volumes.

| Service | Image | Purpose | Default port |
|---------|-------|---------|--------------|
| `postgres` | `pgvector/pgvector:pg16` | State, audit log, LangGraph checkpoints, vectors | `5432` |
| `redis` | `redis:7-alpine` | Queue + per-tenant scheduler (appendonly persistence) | `6379` |
| `minio` | `minio/minio` | S3-compatible asset/creative store | `9000` (API), `9001` (console) |
| `minio-init` | `minio/mc` | One-shot: creates the assets bucket, then exits | — |

## Quick start

```bash
cd infra
cp .env.example .env        # adjust ports/credentials if needed
docker compose up -d        # pull + start everything
bash smoke.sh               # verify all acceptance criteria
```

Tear down:

```bash
docker compose down         # stop containers, keep data
docker compose down -v      # stop AND wipe volumes (fresh pgvector init next up)
```

## What you get

- **Postgres + pgvector** — the `vector` extension is enabled on first boot by
  [`initdb/01-pgvector.sql`](initdb/01-pgvector.sql). Connect with:
  ```
  postgresql://scalers:scalers@localhost:5432/scalers
  ```
- **Redis** — `redis://localhost:6379/0`, appendonly on so queue state survives restarts.
- **MinIO** — S3 API at `http://localhost:9000`, web console at
  `http://localhost:9001` (log in with `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD`).
  The `scalers-assets` bucket is created automatically.

All ports and credentials are configurable in `.env` — change a `*_PORT` if it
clashes with something already running.

## Verifying (acceptance criteria)

`bash smoke.sh` checks each criterion and exits non-zero on any failure:

1. `docker compose up -d` brings all services **healthy** (`docker compose ps`).
2. **pgvector** is available in psql:
   ```bash
   docker exec scalers-postgres psql -U scalers -d scalers \
     -c "SELECT extversion FROM pg_extension WHERE extname='vector';"
   ```
3. **MinIO console** is reachable at <http://localhost:9001>.
4. **Redis** answers `PING`:
   ```bash
   docker exec scalers-redis redis-cli ping   # -> PONG
   ```

## Database schema / migrations

SQL in `initdb/` runs **in filename order on a fresh cluster** (first boot):

| File | Adds |
|------|------|
| `01-pgvector.sql` | the `vector` extension |
| `02-side-effect-boundary.sql` | `side_effect_ledger` + `outbox`, both with `UNIQUE(idempotency_key)` — the exactly-once boundary (systemdesign §3, HARN-04) |

If your data volume already exists, apply new migrations without wiping it:

```bash
bash migrate.sh        # idempotent — re-running is a no-op
```

## Persistence

Data lives in named volumes (`scalers_pgdata`, `scalers_redisdata`,
`scalers_miniodata`) and survives `down`/`up`. The pgvector init SQL only runs on
a **fresh** cluster, so it re-runs only after `down -v`.

## Windows notes

- Named volumes are used for all data (not host bind mounts) to avoid Windows
  file-permission quirks. The only bind mount is the read-only `initdb/` SQL.
- Run `smoke.sh` from **Git Bash** (it needs `curl`, which ships with Git for Windows).
- Requires Docker Desktop with the WSL2 backend.
