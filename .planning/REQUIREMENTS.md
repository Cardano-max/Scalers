# Requirements: Scalers

**Defined:** 2026-06-28
**Core Value:** Reliable, auditable marketing autonomy — the engine does the work and only escalates the uncertain few; it never fires off-brand or unsafe content without operator sign-off.

## v1 Requirements

### Harness (Control layer)
- [ ] **HARN-01**: Deterministic fixed graph skeleton in code; LLM runs only inside bounded cells
- [ ] **HARN-02**: Every LLM cell returns a parser-validated typed schema (no raw model text flows downstream)
- [ ] **HARN-03**: Durable substrate (DBOS) with crash recovery + bounded 3-level recovery (retry → regenerate → human-review)
- [ ] **HARN-04**: Exactly-once side effects (idempotency keys + Postgres unique constraints + outbox)
- [ ] **HARN-05**: Pure-code router decides auto / human-review / regenerate over concrete confidence signals
- [ ] **HARN-06**: Pinned model versions; temp-0 on all decision/classify cells

### Autonomy (Intelligence layer)
- [ ] **AUTON-01**: Cross-family LLM jury scores each action on brand-voice / safety / appropriateness
- [ ] **AUTON-02**: Deterministic confidence computer (self-consistency signals) + calibrated thresholds
- [ ] **AUTON-03**: Deterministic gates (suppression, rate cap, PII redaction, tenant policy, media format)
- [ ] **AUTON-04**: Independent safety classifier can veto and escalate
- [ ] **AUTON-05**: Per-channel autonomy dial (auto vs approve-first + threshold), operator-settable

### Posting engine (Engine 1)
- [ ] **POST-01**: Research → strategy → create pipeline produces an organic post draft
- [ ] **POST-02**: Media/format validation (Reels 9:16 5–90s, image specs, caption/hashtag limits)
- [ ] **POST-03**: Publish to Instagram + Facebook via Meta MCP / Graph API, idempotently
- [ ] **POST-04**: Scheduled posting on per-client cadence

### Outreach engine (Engine 2)
- [ ] **MAIL-01**: Lead intake + dedupe + suppression list
- [ ] **MAIL-02**: Per-prospect research + personalized draft in the configured voice
- [ ] **MAIL-03**: Deliverability QA (SPF/DKIM/DMARC, one-click unsubscribe, complaint/bounce ceilings)
- [ ] **MAIL-04**: Send via Gmail under warmup/caps + capped follow-up sequence

### Engagement engine (Engine 3)
- [ ] **ENG-01**: Meta webhook ingest, deduped on event id
- [ ] **ENG-02**: Conversation-history-aware comment/DM triage (classifier)
- [ ] **ENG-03**: Gated reply with human-like jitter + IG DM 24h-window rules

### Deep research (shared)
- [ ] **RSCH-01**: Pluggable research over Exa / Firecrawl / Reddit with budget caps
- [ ] **RSCH-02**: Competitor / winning-pattern mining + deterministic scoring

### Knowledge layer
- [ ] **KNOW-01**: Vector KB (pgvector) indexing past posts + performance per tenant
- [ ] **KNOW-02**: Brand-voice grounding from past content
- [ ] **KNOW-03**: Feedback loop writes outcomes back to the KB
- [ ] **KNOW-04**: Memory governance / drift control

### API gateway (Capacity layer)
- [ ] **API-01**: NestJS code-first GraphQL queries (overview, reviewQueue, action, runs, run, feed, health, tenant)
- [ ] **API-02**: GraphQL mutations (approve, reject, editDraft, regenerate, setEngineState, setAutonomy, sendCommand)
- [ ] **API-03**: SSE stream (feed.event, action.created, action.updated, run.updated, kpi.updated, health.updated, toast)
- [ ] **API-04**: FastAPI engine surface emitting AG-UI-shaped typed frames

### Operator Console wiring (frontend)
- [ ] **CON-01**: Overview wired to real KPIs / attention / runs / health / feed
- [ ] **CON-02**: Review queue wired to real actions + autonomy decision + approve/edit/regenerate/reject
- [ ] **CON-03**: Live feed wired to the SSE stream
- [ ] **CON-04**: Runs wired to real run trajectory + detail
- [ ] **CON-05**: Command wired to the harness with streamed reply

### Eval & quality
- [ ] **EVAL-01**: Gold set per engine (30–200 expert-labeled examples)
- [ ] **EVAL-02**: Inspect AI eval suite + CI gate (eval-on-every-change)
- [ ] **EVAL-03**: Calibration gates (ECE / F1) via DeepEval
- [ ] **EVAL-04**: Reliability scorecard + OWASP-Top-10-Agentic red-team
- [ ] **EVAL-05**: Measurable targets instrumented (brand-voice ≥90%, classify P/R ≥0.95, latency/cost budgets)

### Infrastructure
- [ ] **INFRA-01**: docker-compose local stack (Postgres+pgvector, Redis, MinIO)
- [ ] **INFRA-02**: Cloudflare tunnel for the portal + Meta webhook
- [ ] **INFRA-03**: Self-hosted MCP servers (meta-mcp, Gmail) + secrets management
- [ ] **INFRA-04**: Per-tenant config / "packs" (generic FE, niche in backend)

## v2 Requirements (deferred)
- **ADS-01**: Paid-ads module via official Meta Ads MCP (behind the same gate)
- **MULTI-01**: Multi-tenant fan-out (Redis pub/sub per run id), multiple operator sessions
- **VOICE-01**: Self-hosted LoRA/QLoRA brand-voice adapter (if prompting caps fidelity)

## Out of Scope

| Feature | Reason |
|---------|--------|
| Booking system / booking loop | Operator removed it — was client context, not this system |
| Multi-tenant SaaS + billing | Engine-first, single client for now |
| Tattoo-specific frontend | Console is generic; niche in backend config |
| AWS / cloud | Local Docker + Cloudflare tunnel only |
| Private-API social tooling | Ban risk; official Graph API only |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| HARN-01..06, INFRA-01, INFRA-04 | Phase 1 | Pending |
| EVAL-01, EVAL-02, EVAL-03, KNOW-01 | Phase 2 | Pending |
| POST-01, POST-02, RSCH-01, KNOW-02 | Phase 3 | Pending |
| API-01, API-02, API-03, API-04, CON-01..05 | Phase 4 | Pending |
| AUTON-01..05 | Phase 5 | Pending |
| POST-03, POST-04, MAIL-03, INFRA-02, INFRA-03 | Phase 6 | Pending |
| MAIL-01, MAIL-02, MAIL-04, ENG-01, ENG-02, ENG-03, RSCH-02, KNOW-03, KNOW-04 | Phase 7 | Pending |
| EVAL-04, EVAL-05 | Phase 8 | Pending |

**Coverage:** v1 requirements: 41 total · Mapped to phases: 41 · Unmapped: 0 ✓

---
*Requirements defined: 2026-06-28*
*Last updated: 2026-06-28 after initialization*
