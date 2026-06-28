# Observability stack (Prometheus + Grafana + Langfuse)

Local Docker, no AWS. Three pieces:

| Piece | Compose file | Purpose | Port (default) |
|---|---|---|---|
| Prometheus | `docker-compose.observability.yml` | scrape + store engine metrics | 9090 |
| Grafana | `docker-compose.observability.yml` | operator dashboards | **3001** (not 3000 — avoids Langfuse clash) |
| Langfuse | `docker-compose.langfuse.yml` | LLM traces / prompt versions / eval results (canonical per rvy.1 ADR) | 3000 |

Metrics (Prometheus/Grafana) are the **operator dashboard** source. Tracing/evals (Langfuse) are **best-effort, non-gating** — if Langfuse is down, metrics + the deterministic eval gate are unaffected (rvy.1 ADR).

## Bring it up
```bash
cd infra
# metrics + dashboards
docker compose -f docker-compose.observability.yml up -d
#   Grafana    → http://localhost:3001   (admin / admin, override via GRAFANA_PASSWORD)
#   Prometheus → http://localhost:9090
# traces/evals (separate, heavier stack)
docker compose --env-file .env -f docker-compose.langfuse.yml up -d   # → http://localhost:3000
```
Grafana auto-provisions the Prometheus datasource and loads the **"Scalers — Engine Overview"** dashboard (no click-ops). All services `restart: unless-stopped` → reboot-safe.

## Metrics contract for eng  ⟵ REQUIRED for the dashboard to show data
The engine is **not yet instrumented**. Prometheus scrapes `host.docker.internal:8000/metrics`
(`prometheus/prometheus.yml`, job `scalers-engine`) — until the engine exposes that endpoint the
target shows **DOWN** and panels read empty. That is expected and **non-gating**.

To light up the dashboard, the engine must expose a Prometheus `/metrics` endpoint (e.g.
`prometheus_client.make_asgi_app()` mounted on the FastAPI app at `:8000`) emitting these series.
Names/labels are the contract the starter dashboard queries — keep them stable:

| Metric | Type | Labels | Powers panel |
|---|---|---|---|
| `scalers_decisions_total` | counter | `outcome`=`auto`\|`review`\|`off`, `tenant`, `channel` | Autonomy % |
| `scalers_queue_depth` | gauge | `queue`, `tenant` | Queue depth |
| `scalers_complaints_total` | counter | `tenant`, `channel` | Complaint rate (numerator) |
| `scalers_actions_published_total` | counter | `tenant`, `channel` | Complaint rate (denominator) |
| `scalers_publish_quota_used` | gauge | `tenant`, `channel` | Publish quota % used |
| `scalers_publish_quota_limit` | gauge | `tenant`, `channel` | Publish quota % used (from `content_publishing_limit`) |
| `scalers_run_latency_seconds` | histogram | `tenant`, (optional) `node` | Run latency p50/p95/p99 |

Notes:
- Autonomy % = `auto / all` decisions; maps to the per-channel autonomy mode (`auto`/`review`/`off`) in the tenant pack.
- Publish quota: set `_limit` from the IG `content_publishing_limit` query (default 25/24h); `_used` from publishes in the rolling window. Dashboard shows `used/limit %`.
- Latency as a histogram so p-quantiles work (`histogram_quantile` over `_bucket`).
- After editing `prometheus.yml`: `curl -XPOST http://localhost:9090/-/reload`.

Filed as a follow-up so it isn't lost: see the eng instrumentation bead referenced on CustomerAcq-3bu.

## Feeds
Console **System-health** card + the obs slice (`kkg`) read from these same series / Grafana.
