# Scalers Operator Console — Local Run Guide

**For:** Client demo of the Scalers Operator Console running against real Postgres data, executing real Gmail sends, and showing the complete decision → approval → send → activity trace flow.

**Outcome:** The operator console at `http://localhost:3000` reviews a generated action, clicks Approve & send, a real Gmail sends, and the console displays the execution trace, jury card, and deep link to the email.

---

## Prerequisites

### Tools & Runtimes
Ensure these are installed and on your PATH:

- **Docker Desktop** with WSL2 backend (for Postgres, Redis, MinIO)
- **uv** (Python package manager) — [install](https://docs.astral.sh/uv/)
- **Node 20+** (for the Next.js console) — [download](https://nodejs.org/)
- **Python 3.11+** via the Windows `py` launcher (comes with Python installations)
- **Git for Windows** (for Git Bash if using bash commands)

### Credentials File

The operator's API keys and secrets live in a **gitignored** credentials file that **must never be committed**:

```
C:\Users\Links\Pictures\.finalenv.txt
```

This file contains:
```
ANTHROPIC_API_KEY=sk-ant-...
GMAIL_REFRESH_TOKEN=1//0...
GMAIL_CLIENT_ID=...@developer.gserviceaccount.com
GMAIL_CLIENT_SECRET=...
LADIES8391_FB_PAGE_TOKEN=...  (may be expired; requires re-mint)
META_APP_ID=...
META_APP_SECRET=...
IG_BUSINESS_ACCOUNT_ID=...
```

**Status (verified 2026-06-29):**
- Gmail: VALID ✓ (refresh token works, scope `gmail.send`)
- Facebook page token: EXPIRED (requires operator to mint a fresh long-lived page token)
- Instagram: INVALID (gated until fresh Meta credentials)

### DBeaver (Optional but Recommended)

For inspecting the Postgres database during the demo:
- [DBeaver Community Edition](https://dbeaver.io/) — PostgreSQL driver included

### Postman (Optional but Recommended)

For manually testing GraphQL queries and the SSE endpoint:
- [Postman](https://www.postman.com/) — desktop or web

---

## Step 1: Start the Local Infrastructure (Postgres + Redis + MinIO)

Navigate to the `infra/` subdirectory and bring up Docker Compose:

```bash
cd eng5/src/infra

# Copy the example .env (adjust ports if any clash locally)
cp .env.example .env

# Start all services: Postgres + pgvector, Redis, MinIO
docker compose up -d

# Verify all are healthy
docker ps
```

Expected output (all services HEALTHY):
```
CONTAINER ID   IMAGE                                        STATUS          NAMES
<id>           pgvector/pgvector:0.8.3-pg16               Up x seconds (healthy)   scalers-postgres
<id>           redis:7.4.9-alpine                          Up x seconds (healthy)   scalers-redis
<id>           minio/minio:RELEASE.2025-09-07...          Up x seconds (healthy)   scalers-minio
```

### Service Endpoints

| Service | Purpose | Endpoint | Default Creds |
|---------|---------|----------|----------------|
| **Postgres** | State, audit, vectors | `localhost:5432` | user: `scalers`, password: `scalers`, db: `scalers` |
| **Redis** | Queue + scheduler | `localhost:6379` | (no auth by default) |
| **MinIO API** | S3-compatible asset store | `http://localhost:9000` | user: `scalers`, password: `scalers-dev-secret` |
| **MinIO Console** | Web UI | `http://localhost:9001` | user: `scalers`, password: `scalers-dev-secret` |

---

## Step 2: Apply the Database Schema

The Postgres container needs the schema (pgvector extension, tables, indexes). All schema files are idempotent (CREATE ... IF NOT EXISTS), so re-running is safe.

### Option A: Auto-apply on fresh start (already done)

When you first ran `docker compose up -d`, the init scripts in `infra/initdb/` were mounted read-only and ran automatically. Verify the schema exists:

```bash
docker exec -i scalers-postgres psql -U scalers -d scalers -c "\dt"
```

Expected output: tables like `actions`, `autonomy_decisions`, `side_effect_ledger`, etc.

### Option B: Manual schema application (if needed)

If you brought up an existing container or need to apply new migrations:

```bash
cd eng5/src/infra
bash migrate.sh
```

Or apply individual schema files:

```bash
for f in initdb/*.sql; do
  echo "Applying $f..."
  docker exec -i scalers-postgres psql -U scalers -d scalers < "$f"
done
```

---

## Step 3: Start the Backend (FastAPI Engine + GraphQL + SSE)

### Set up the Python environment

Navigate to the engine directory and install dependencies:

```bash
cd eng5/src/engine

# Install dependencies via uv (single invocation for both base + postgres optional extra)
uv sync --extra postgres

# (Optional) If you want observability (Langfuse), add it:
# uv sync --extra postgres --extra observability
```

### Load credentials into the environment

Before running the engine, load the secrets from the credentials file into the shell environment. This is how the backend accesses `ANTHROPIC_API_KEY` and the connector tokens.

**On Git Bash or PowerShell:**

```bash
# Read the credentials file and export each KEY=VALUE line
while IFS='=' read -r key value; do
  export "$key"="$value"
done < /c/Users/Links/Pictures/.finalenv.txt

# Verify they're loaded
echo "ANTHROPIC_API_KEY is set: ${ANTHROPIC_API_KEY:0:10}..."
```

(Or manually: `export ANTHROPIC_API_KEY=sk-ant-... GMAIL_REFRESH_TOKEN=... `)

### Start the engine

```bash
cd eng5/src/engine

# Set the database URL (points to the local Postgres from Step 1)
export ENGINE_DATABASE_URL=postgresql://scalers:scalers@localhost:5432/scalers

# Start the uvicorn server
uv run uvicorn main:app --host 127.0.0.1 --port 8000
```

Expected output:
```
INFO:     Uvicorn running on http://127.0.0.1:8000
```

### Verify the engine is alive

In a new terminal:

```bash
curl http://localhost:8000/healthz
```

Expected:
```json
{
  "status": "ok",
  "models": {
    "default": "claude-opus-4-8",
    "decision": "claude-haiku-4-5",
    "jury": "claude-opus-4-8"
  },
  "temperature": 0.0,
  "checkpointer": "postgres"
}
```

### Engine Endpoints (for reference)

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/healthz` | GET | Liveness + config probe |
| `/metrics` | GET | Prometheus metrics (for monitoring) |
| `/graphql` | POST | GraphQL mutations + queries (console uses this) |
| `/sse/stream` | GET | SSE: all events (feed, actions, runs, health, toasts) |
| `/sse/feed` | GET | SSE: feed-only (convenience stream) |

---

## Step 4: Start the Frontend (Next.js Console)

Navigate to the web directory and configure it for live backend:

```bash
cd eng5/src/web

# Create .env.local (console env configuration)
cat > .env.local << 'EOF'
NEXT_PUBLIC_DATA_SOURCE=live
NEXT_PUBLIC_GRAPHQL_URL=http://localhost:8000/graphql
NEXT_PUBLIC_SSE_URL=http://localhost:8000/sse/stream
NEXT_PUBLIC_SSE_FEED_URL=http://localhost:8000/sse/feed
NEXT_PUBLIC_TENANT_ID=ladies8391
EOF

# Install npm dependencies
npm install

# Start the dev server
npm run dev
```

Expected output:
```
> Local:   http://localhost:3000
```

### Open the console

In your browser:
```
http://localhost:3000
```

You should see the **Operator Console** (empty initially, since no decisions have been generated yet).

---

## Step 5: Inspect the Database (DBeaver)

### Create a Postgres connection in DBeaver

1. Open DBeaver.
2. **File → New → Database Connection**
3. Select **PostgreSQL** → **Next**
4. Fill in:
   - **Server Host:** `localhost`
   - **Port:** `5432`
   - **Database:** `scalers`
   - **Username:** `scalers`
   - **Password:** `scalers`
5. **Test Connection** (should succeed)
6. **Finish**

### Key tables to inspect during the demo

| Table | Purpose | Key Columns |
|-------|---------|-------------|
| `actions` | Review queue + sent results | `id, tenant_id, decision_id, type, channel, status, draft, deep_link, created_at` |
| `autonomy_decisions` | Per-decision metadata (jury result, confidence, gates) | `decision_id, tenant_id, confidence, routed_to, gate_labels` |
| `autonomy_jury` | Per-judge scores (voice, safety, appropriateness) | `decision_id, judge_id, voice_score, safety_score, appr_score` |
| `runs` | Per-run execution trajectory (JSONB steps) | `id, tenant_id, status, steps, created_at` |
| `outbox` | Side-effect intent queue (exactly-once boundary) | `id, idempotency_key, channel, payload, status` |
| `side_effect_ledger` | Sent side-effect log (real provider IDs) | `idempotency_key, channel, provider_id, status` |

### Example SELECT queries

**List all pending actions for ladies8391:**

```sql
SELECT id, type, channel, status, target, deep_link, created_at
FROM actions
WHERE tenant_id = 'ladies8391' AND status = 'pending'
ORDER BY created_at DESC;
```

**View a specific action + its jury card:**

```sql
SELECT
  a.id, a.type, a.channel, a.status, a.draft,
  d.decision_id, d.confidence, d.routed_to,
  j.judge_id, j.voice_score, j.safety_score, j.appr_score
FROM actions a
LEFT JOIN autonomy_decisions d ON a.decision_id = d.decision_id
LEFT JOIN autonomy_jury j ON d.decision_id = j.decision_id
WHERE a.tenant_id = 'ladies8391'
ORDER BY a.created_at DESC
LIMIT 10;
```

**Check exactly-once status (side_effect_ledger):**

```sql
SELECT idempotency_key, channel, provider_id, status, result, created_at
FROM side_effect_ledger
WHERE channel = 'gmail'
ORDER BY created_at DESC;
```

**View run execution trace:**

```sql
SELECT id, status, steps, created_at
FROM runs
WHERE tenant_id = 'ladies8391'
ORDER BY created_at DESC
LIMIT 1;
```

---

## Step 6: Test with Postman (Optional)

### Import the Postman Collection

Two files ship alongside this guide in the demo-evidence folder:

- `Scalers-Operator-Console.postman_collection.json` — pre-built GraphQL queries/mutations
- `Scalers-Local.postman_environment.json` — environment variables (backend URL, tenant ID, etc.)

**Postman import steps:**

1. **File → Import**
2. Upload the `.postman_collection.json` file
3. **File → Import** again, upload the `.postman_environment.json`
4. In Postman, select the **Scalers-Local** environment from the dropdown (top right)
5. You're ready to run requests

### Key Requests (pre-built in the collection)

**1. Check the review queue:**

- **Request:** `GraphQL → reviewQueue`
- **Body:** (pre-filled) GraphQL query to list all pending actions for the active tenant
- **Expected:** Returns a list of pending actions (empty if none yet)

**2. Approve and send an action:**

- **Request:** `GraphQL → approveAction`
- **Body:** (pre-filled) Mutation with action ID and tenant ID
- **Note:** This **fires the real Gmail connector**. The email goes to the recipient in the action's `target` field.
- **Expected:** Returns the updated action with status `sent`, a `deep_link` URL to the real Gmail message, and execution trace

**3. Stream live SSE events:**

- **Request:** `SSE → Live SSE stream`
- **URL:** `GET http://localhost:8000/sse/stream?tenantId=ladies8391`
- **Expected:** Streaming Event Source that emits:
  - `feed.event` — activity feed updates (new decisions)
  - `action.created` — new action entered the review queue
  - `action.updated` — action status changed (pending→sent, etc.)
  - `run.updated` — run execution progressed
  - `health.updated` — system health

**To test SSE in Postman:**
1. Open the "Live SSE stream" request
2. Click **Send**
3. Postman will stream events as they arrive; do not close this tab
4. In another terminal or the console UI, trigger an approval
5. Watch the SSE tab update in real time with the result

---

## Step 7: End-to-End Demo Flow

### Seed a decision (generate an action)

For now, actions are seeded manually in the database. The operator will do this via an API call or a separate generation service.

**Manual seed (DBeaver or psql):**

```sql
-- Insert a decision (autonomy_decisions)
INSERT INTO autonomy_decisions (
  tenant_id, decision_id, routed_to, confidence, temperature, 
  model_decision, model_reasoning, gate_labels, outcome_kind, created_at
) VALUES (
  'ladies8391',
  'dec_' || substr(md5(random()::text), 1, 12),
  'REVIEW',
  0.87,
  0.0,
  'SEND',
  'The outreach is timely and personalized.',
  '[]'::jsonb,
  'amber',
  now()
);

-- Capture the decision_id from the INSERT result (or query it back)

-- Insert jury scores
INSERT INTO autonomy_jury (decision_id, judge_id, voice_score, safety_score, appr_score)
VALUES (
  '<decision_id>',  -- from above
  'Judge-1',
  0.92, 0.95, 0.75
);

-- Insert the action (what the console shows in the review queue)
INSERT INTO actions (
  id, tenant_id, decision_id, type, channel, worker, target, subject, draft, status, 
  idempotency_key, conf, threshold, esc_kind, esc_label, created_at
) VALUES (
  'act_' || substr(md5(random()::text), 1, 12),
  'ladies8391',
  '<decision_id>',
  'outreach',
  'gmail',
  'Outreach',
  'target@example.com',
  'Exclusive Offer: Grow Your Tattoo Studio',
  'Hi there! We noticed you run an amazing tattoo studio. We have a special offer...',
  'pending',
  'idempotency_' || substr(md5(random()::text), 1, 12),
  0.87,
  0.85,
  'confidence',
  'below_threshold',
  now()
);
```

### Review in the Console

1. Refresh http://localhost:3000 (or watch the live SSE stream)
2. The new action appears in the **Review Queue** section
3. Click the action card to expand it
4. Inspect:
   - The draft email body
   - The **Jury Card** showing each judge's voice/safety/appropriateness scores
   - The confidence bar (87%) vs. the approval threshold (85%)
   - Any escalation tags (e.g., "below_threshold")

### Approve & Send

1. Click the **Approve & Send** button on the action card
2. The console calls the `approveAction` GraphQL mutation with the action ID
3. The backend:
   - Marks the action as `approved`
   - Calls the real Gmail connector with the refresh token from `.finalenv.txt`
   - Sends the email to the recipient
   - Records the provider response (Gmail message ID) in `side_effect_ledger`
   - Flips the action status to `sent`
   - Writes the `deep_link` (Gmail message URL)
4. Watch the console update live:
   - Status badge changes from "Pending Review" → "Sent"
   - **Activity** section shows the execution trace (latency, model, jury) and a **View Email** link
   - SSE events stream the entire sequence (if you're watching)

### Verify in DBeaver

**Check the action row:**

```sql
SELECT id, status, deep_link, approved_at, sent_at, engagement
FROM actions
WHERE id = '<action_id>'
AND tenant_id = 'ladies8391';
```

Expected: `status = 'sent'`, `deep_link` is a Gmail URL, `sent_at` is recent.

**Check the side-effect ledger (exactly-once proof):**

```sql
SELECT idempotency_key, channel, provider_id, status, result
FROM side_effect_ledger
WHERE idempotency_key LIKE 'idempotency_%'
ORDER BY created_at DESC
LIMIT 1;
```

Expected: `status = 'SENT'`, `provider_id` is the Gmail message ID.

**Check the outbox (at-least-once queue):**

```sql
SELECT idempotency_key, status, attempts, created_at
FROM outbox
WHERE channel = 'gmail'
ORDER BY created_at DESC
LIMIT 1;
```

Expected: `status = 'SENT'`.

---

## Troubleshooting

### Port Already in Use

If Docker or a local service is already using a port:

**Postgres (5432), Redis (6379), MinIO API (9000), MinIO Console (9001):**

On Windows (PowerShell):
```powershell
netstat -ano | findstr :5432   # Find PID using port 5432
taskkill /PID <pid> /F         # Kill the process
```

Or adjust the port in `infra/.env` before `docker compose up`:
```
POSTGRES_PORT=5433
REDIS_PORT=6380
MINIO_API_PORT=9002
MINIO_CONSOLE_PORT=9003
```

**Next.js (3000), FastAPI (8000):**

Kill the process or change the port:
```bash
# Frontend: add to the dev command
npm run dev -- -p 3001

# Backend: change the uvicorn port
uv run uvicorn main:app --host 127.0.0.1 --port 8001
```

### Backend Not Picking Up New Credentials

The engine reads `ANTHROPIC_API_KEY` from the environment at startup. If you updated `.finalenv.txt`:

1. Stop the backend (Ctrl+C in its terminal)
2. Re-export the credentials:
   ```bash
   while IFS='=' read -r key value; do
     export "$key"="$value"
   done < /c/Users/Links/Pictures/.finalenv.txt
   ```
3. Restart the backend:
   ```bash
   cd eng5/src/engine
   uv run uvicorn main:app --host 127.0.0.1 --port 8000
   ```

### SSE Not Streaming

**Check CORS:**
Ensure the backend is running and CORS is enabled for `http://localhost:3000`.

In `engine/obsapi/mount.py`:
```python
CONSOLE_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]
```

**Check the SSE URL:**
Make sure `.env.local` has the correct endpoint:
```
NEXT_PUBLIC_SSE_URL=http://localhost:8000/sse/stream
NEXT_PUBLIC_SSE_FEED_URL=http://localhost:8000/sse/feed
```

**Check the browser console:**
Open DevTools (F12 → Console) and look for errors. Common issues:
- Network tab shows 404 on `/sse/stream` → backend not running
- CORS error → update `CONSOLE_ORIGINS`

### Stale Next.js Cache

If the console looks broken or stuck:

```bash
cd eng5/src/web
rm -rf .next
npm run dev
```

### Database Connection Errors

**Can't connect to Postgres:**

```bash
# Verify the container is running
docker ps | grep scalers-postgres

# Check logs
docker logs scalers-postgres

# Try connecting manually
docker exec -it scalers-postgres psql -U scalers -d scalers -c "SELECT version();"
```

**"Relation does not exist" errors:**

Schema wasn't applied. Run:
```bash
cd eng5/src/infra
bash migrate.sh
```

### Gmail Send Fails

**Symptoms:** Approval succeeds in the console, but status flips to `failed` instead of `sent`.

**Check the error:**

```sql
SELECT id, status, last_error, updated_at
FROM actions
WHERE tenant_id = 'ladies8391' AND status = 'failed'
ORDER BY updated_at DESC
LIMIT 1;
```

**Common causes:**

1. **Invalid refresh token** — `.finalenv.txt` has an expired or invalid `GMAIL_REFRESH_TOKEN`
   - Solution: Operator must regenerate the Gmail refresh token and update `.finalenv.txt`

2. **Missing scopes** — Token doesn't have `gmail.send` scope
   - Solution: Re-create the token with the `gmail.send` scope

3. **Daily quota exceeded** — Gmail API has a per-day send limit (usually thousands)
   - Solution: Wait until the next day or contact Google Cloud Support

4. **Recipient error** — The `target` field (recipient email) is invalid
   - Solution: Verify the email address in the action's `target` column

### Memory Leak or Slow Performance

If the console or backend becomes sluggish:

**Restart the backend:**
```bash
# Ctrl+C in the engine terminal
cd eng5/src/engine
uv run uvicorn main:app --host 127.0.0.1 --port 8000
```

**Restart the frontend:**
```bash
# Ctrl+C in the web terminal
cd eng5/src/web
npm run dev
```

**Restart Docker services (safe, data persists):**
```bash
docker compose -f eng5/src/infra/docker-compose.yml down
docker compose -f eng5/src/infra/docker-compose.yml up -d
```

---

## Architecture Reference

### Services & Ports

```
┌─────────────────────────────────────────────────────────┐
│  Browser                                                │
│  http://localhost:3000 (Next.js Console)               │
└───────────────────────┬─────────────────────────────────┘
                        │
                        ├─ GraphQL POST /graphql
                        ├─ SSE GET /sse/stream
                        └─ SSE GET /sse/feed
                        ↓
┌─────────────────────────────────────────────────────────┐
│  Backend                                                │
│  http://localhost:8000 (FastAPI Engine)                │
│  - GraphQL (Strawberry) + SSE (mounted via obsapi)     │
│  - Connectors (Gmail, FB, IG)                          │
│  - Action approval & publish flow                      │
└───────────────────────┬─────────────────────────────────┘
                        │
        ┌───────────────┼───────────────┐
        │               │               │
        ↓               ↓               ↓
┌──────────────┐ ┌────────────┐ ┌──────────────┐
│  Postgres    │ │  Redis     │ │  MinIO       │
│  localhost:5432 │  localhost:6379 │ localhost:9000/9001 │
└──────────────┘ └────────────┘ └──────────────┘
 actions       queue/cache     asset store
 decisions     scheduler       (optional)
 runs
 audit log
```

### Data Flow (Demo Path)

1. **Seed decision** → `autonomy_decisions` + `autonomy_jury` + `actions` (pending)
2. **Console reads** → GraphQL `reviewQueue` query → lists actions where `status='pending'`
3. **Operator clicks Approve** → `approveAction` mutation → backend
4. **Backend approves** → marks action `approved`, calls Gmail connector
5. **Gmail send** → connector calls real Gmail API with refresh token from env
6. **Record result** → `side_effect_ledger` (exactly-once), `actions.status='sent'`, `deep_link=URL`
7. **Console updates** → SSE `action.updated` event streams, UI re-renders
8. **Activity shows trace** → run execution (if linked) + jury card + View Email link

---

## Notes for the Operator

- **Autonomy is HOLD.** The console shows autonomy state but does not enable auto-send. Every send is human-approved.
- **Real sends.** When you click Approve, the email/post/reply goes to the real recipient/target. Use deliberately.
- **Credentials never in code.** All secrets (API keys, tokens) live in `.finalenv.txt`, which is gitignored and never committed.
- **Exactly-once guarantee.** The `side_effect_ledger` + `outbox` tables enforce exactly-once delivery. A retry or re-approval will not double-send.
- **Activity trace is real data.** The jury scores, span trees, and deep links come from actual decision metadata in Postgres, not fabricated for the demo.

---

## Quick Reference Commands

```bash
# Start everything
cd eng5/src/infra && docker compose up -d && cd ../../

# Load credentials (Git Bash)
while IFS='=' read -r key value; do
  export "$key"="$value"
done < /c/Users/Links/Pictures/.finalenv.txt

# Start backend
cd eng5/src/engine && export ENGINE_DATABASE_URL=postgresql://scalers:scalers@localhost:5432/scalers && uv run uvicorn main:app --host 127.0.0.1 --port 8000

# Start frontend (new terminal)
cd eng5/src/web && npm install && npm run dev

# Verify backend
curl http://localhost:8000/healthz

# Tail postgres logs
docker logs -f scalers-postgres

# Open Postgres CLI
docker exec -it scalers-postgres psql -U scalers -d scalers

# List pending actions
docker exec -i scalers-postgres psql -U scalers -d scalers -c "SELECT id, type, status, target FROM actions WHERE status='pending';"

# Tear down (keeps data)
docker compose -f eng5/src/infra/docker-compose.yml down

# Full reset (wipe data)
docker compose -f eng5/src/infra/docker-compose.yml down -v
```

---

**Last Updated:** 2026-06-29 | **Version:** 0.1.0
