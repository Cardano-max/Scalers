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
Grafana auto-provisions the Prometheus datasource and loads two dashboards (no click-ops):
- **"Scalers — Engine Overview"** (`scalers-overview.json`) — autonomy %, queue, complaint rate, publish quota %, run latency.
- **"Scalers — Engine Internals"** (`scalers-engine-internals.json`) — run counts by status, cell latency p95 by cell, autonomy decision mix, gate pass/fail, side-effect outcomes.

All services `restart: unless-stopped` → reboot-safe.

## Metrics contract (engine `/metrics`)
The engine **is instrumented** (CustomerAcq-13u, Scalers PR #43): `engine/metrics.py` exposes
`/metrics` on the FastAPI portal — **11 series live**, target verified **UP**. Prometheus scrapes
`host.docker.internal:8000/metrics` (`prometheus/prometheus.yml`, job `scalers-engine`).

> **Deploy requirement (eng3):** run the engine bound to **`0.0.0.0:8000`** (not `127.0.0.1`), or
> the in-container Prometheus can't reach it over `host.docker.internal` and the target shows DOWN.
> Factor this into the run command / deploy compose.

Series — names/labels are a **contract** the dashboards query; keep them stable:

| Metric | Type | Labels | Powers |
|---|---|---|---|
| `scalers_decisions_total` | counter | `outcome`=`auto`\|`review`\|`off`, `tenant`, `channel` | Autonomy % / decision mix |
| `scalers_queue_depth` | gauge | `queue`, `tenant` | Queue depth |
| `scalers_complaints_total` | counter | `tenant`, `channel` | Complaint rate (numerator) |
| `scalers_actions_published_total` | counter | `tenant`, `channel` | Complaint rate (denominator) |
| `scalers_publish_quota_used` | gauge | `tenant`, `channel` | Publish quota % used |
| `scalers_publish_quota_limit` | gauge | `tenant`, `channel` | Publish quota % used (from `content_publishing_limit`) |
| `scalers_run_latency_seconds` | histogram | `tenant` | Run latency p50/p95/p99 |
| `scalers_runs_total` | counter | `tenant`, `status` (e.g. `completed`/`failed`) | Run counts / failure % |
| `scalers_cell_latency_seconds` | histogram | `cell` | Cell latency p95 |
| `scalers_gate_checks_total` | counter | `tenant`, `gate`, `result`=`pass`\|`fail` | Gate pass/fail |
| `scalers_side_effects_total` | counter | `tenant`, `channel`, `outcome` (e.g. `sent`/`failed`) | Side-effect outcomes |

Notes:
- `status` (`scalers_runs_total`) and `outcome` (`scalers_side_effects_total`) are **free-form** strings
  emitted by callers; alert failure-regexes track eng3's current vocabulary and should be re-confirmed as
  emit points are added (see `prometheus/alerts.yml` header).
- Latencies are histograms so p-quantiles work (`histogram_quantile` over `_bucket`).
- After editing `prometheus.yml`/`alerts.yml`: `curl -XPOST http://localhost:9090/-/reload`.

## Alerts
`prometheus/alerts.yml` (wired via `rule_files` in `prometheus.yml`) — Prometheus **evaluates** these and
shows them at `/alerts` + `/api/v1/rules` with no extra component. Covers: engine-target-down, high run
failure rate, run/cell latency p95, gate failure spike, side-effect failure rate, complaint rate, publish
quota near limit. **Notification routing needs Alertmanager** (not wired — add if paging is wanted).

> Canonical failure token is `failed` in both `scalers_runs_total.status` and `scalers_side_effects_total.outcome`
> (confirmed with eng3). The run-`failed` and side-effect paths land in **Phase 6** (the real publish loop), so
> `HighRunFailureRate` and `SideEffectFailureRate` read **empty until then — expected, not a broken alert**.

## Feeds
Console **System-health** card + the obs slice (`kkg`) read from these same series / Grafana.
