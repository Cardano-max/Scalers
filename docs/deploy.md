# Deploying Scalers (engine + console + Postgres)

The system is two deployables plus a database:

| Piece | What it is | Where it can run |
|---|---|---|
| `engine/` | Python FastAPI — GraphQL/SSE, studio agents, campaign runs (long-lived server with background work) | Any Docker host: **Render**, Railway, Fly.io, a VPS. **Not** Vercel/serverless — runs and SSE streams outlive a serverless request. |
| `web/` | Next.js operator console (production build verified) | **Vercel** (natural fit) or the same Docker host |
| Postgres | Single source of truth; pgvector recommended (semantic recall degrades honestly without it) | Render Postgres, **Neon**, Supabase, Railway PG — anything with `CREATE EXTENSION vector` |

> Project constraint note (CLAUDE.md): the documented client posture is *local
> Docker + Cloudflare tunnel; no AWS*. Cloud hosting is an operator choice on
> top of that — Option C below is the constraint-conformant path.

---

## Option A — everything on Render (one click)

1. Push this repo to GitHub (already done if you're reading this on the PR).
2. Render dashboard → **New → Blueprint** → pick this repo. Render reads
   `render.yaml`: a pgvector-capable Postgres (`scalers-db`), the engine
   (Docker, `Dockerfile.engine`, `/healthz` health check, 5 GB persistent disk
   on `engine/var` for uploaded artwork), and the console (Node).
3. Paste the secrets when prompted (they are `sync: false` — never in git):
   `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `FIRECRAWL_API_KEY`, the Meta pair
   (`META_PAGE_TOKEN`, `META_IG_USER_ID`, `META_APP_ID`, `META_APP_SECRET`),
   the ladies8391 tokens, Gmail OAuth trio, `SMTP_SENDER`/`SMTP_APP_PASSWORD`,
   `GMAIL_REDIRECT_TO`.
4. First boot runs `deploy_migrate.py` automatically: the full
   `infra/initdb/*.sql` chain + all 24 runtime store bootstraps (verified: a
   fresh database reaches 50 tables in one pass). Re-runs are idempotent.
5. After the console service exists, set `CONSOLE_ORIGINS` on the engine to the
   console's URL **only if** you point the console at the engine cross-origin;
   the blueprint default proxies same-origin via `STUDIO_BACKEND_ORIGIN`, which
   needs no CORS and keeps SSE unbuffered.

## Option B — console on Vercel, engine on Render/Railway, DB on Neon

1. **DB**: create a Neon (or Supabase) Postgres; both support pgvector.
2. **Engine**: deploy `Dockerfile.engine` on Render (delete the console service
   from `render.yaml`) or Railway (`railway up`, service root `/`, dockerfile
   `Dockerfile.engine`). Set `ENGINE_DATABASE_URL` to the Neon connection
   string + the same secrets as Option A.
3. **Console on Vercel**: import the repo, set **Root Directory = `web`**.
   Environment variables:
   - `STUDIO_BACKEND_ORIGIN` = the engine's public URL (server-side rewrite
     target). Vercel proxies `/studio`, `/graphql`, `/sse` same-origin.
   - `NEXT_PUBLIC_DATA_SOURCE=live`, `NEXT_PUBLIC_TENANT_ID=ladies8391`.
   - If SSE feels laggy through Vercel's proxy, bypass it: set
     `NEXT_PUBLIC_GRAPHQL_URL`, `NEXT_PUBLIC_SSE_URL`, `NEXT_PUBLIC_SSE_FEED_URL`
     (and `NEXT_PUBLIC_STUDIO_AGUI_URL`) to absolute engine URLs **and** add the
     Vercel domain to the engine's `CONSOLE_ORIGINS` (comma-separated env var;
     explicit origins only, wildcards are refused by design).
4. The console production build is verified (`npm run build` passes clean).

## Option C — client-constraint path (local Docker + Cloudflare tunnel)

```bash
cd infra && docker compose up -d          # postgres(pgvector) + redis + minio
cd ../engine && uv sync --extra postgres
cp /path/to/operator/.env ../.env          # secrets, gitignored
ENGINE_DATABASE_URL=postgresql://scalers:scalers@localhost:5432/scalers \
  uv run python deploy_migrate.py          # same runner, same result
uv run uvicorn main:app --port 8010 &
cd ../web && npm ci && npm run build && npm run start &
cloudflared tunnel --url http://localhost:3000
```

---

## After first boot (any option)

1. **Tenant registry** — sends are fail-closed until the tenant has a registry
   row. Seed it (test mode ON, allowlist = operator inbox):
   ```python
   from tenants.store import upsert_tenant
   upsert_tenant("ladies8391", name="Ladies First Studio",
                 test_mode=True, allowlist=["operator@yourdomain.com"])
   ```
2. **Verify** `GET /healthz` shows `"checkpointer":"postgres"` and
   `"modelKeyPresent":true`; open the console → status pill reads
   `Engine · live`.
3. **Test-mode posture** (defaults, verify before going live): tenant
   `test_mode=true`, drafts stage HELD, public posts refuse publish without
   explicit Live, non-allowlisted recipients refused fail-closed.
4. **Embedder**: `SCALERS_EMBEDDER=deterministic` (offline stub) is the boot
   default in the image. For real semantic recall set it to `fastembed` once
   the host permits the one-time model download.

## Honest limits

- **Vercel cannot host the engine.** Campaign runs take minutes, SSE streams
  are long-lived, and drafts are produced by background work — a serverless
  function would be killed mid-run. Console on Vercel: great. Engine: Docker.
- **Uploaded artwork lives on disk** (`engine/var/artifacts`). Without the
  persistent disk (Option A includes one) uploads survive requests but not
  redeploys. (MinIO from the local compose is the eventual object-store path.)
- **Redis/MinIO are not required** for the current verified feature set; the
  compose file provisions them for the parts that use them locally.
- Nothing here was deployed *from* the development sandbox (no hosting
  credentials there, by design). What IS verified: the migration runner
  bootstraps a fresh Postgres end-to-end, the console production build passes,
  and the engine serves all verified flows against a real Postgres.
