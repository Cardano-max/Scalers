# Roadmap: Scalers

**Created:** 2026-06-28 · **Granularity:** coarse · **8 phases** · 41/41 v1 requirements mapped ✓

Incremental, fast-ship order. Each phase ends shippable/demoable. The senior-ML critique gate ("gold set before scaling") is honored by landing the eval spine in Phase 2, right after foundations.

| # | Phase | Goal | Requirements |
|---|-------|------|--------------|
| 1 | Foundations & Control core | The deterministic harness runs a typed cell end-to-end with durable, exactly-once execution | HARN-01..06, INFRA-01, INFRA-04 |
| 2 | Eval spine & gold set | Eval-on-every-change works against a real gold set with calibration gates | EVAL-01, EVAL-02, EVAL-03, KNOW-01 |
| 3 | First vertical slice (posting, mock tooling) | One engine produces a real, validated post draft that lands in the review queue | POST-01, POST-02, RSCH-01, KNOW-02 |
| 4 | Console + API wiring | The locked Operator Console runs on live GraphQL + SSE for the slice | API-01..04, CON-01..05 |
| 5 | Autonomy engine | Jury + calibrated confidence + gates + safety route actions auto vs review | AUTON-01..05 |
| 6 | Real tooling & deliverability | Real IG/FB publishing + Gmail deliverability through exactly-once side effects | POST-03, POST-04, MAIL-03, INFRA-02, INFRA-03 |
| 7 | Remaining engines, research & knowledge | Outreach + engagement engines + shared research + feedback/memory all live | MAIL-01, MAIL-02, MAIL-04, ENG-01..03, RSCH-02, KNOW-03, KNOW-04 |
| 8 | Harden & scale | Reliability + red-team + latency/cost budgets met; ready beyond smoke test | EVAL-04, EVAL-05 |

---

## Phase Details

### Phase 1: Foundations & Control core
Goal: The deterministic harness runs a typed LLM cell end-to-end with durable, exactly-once execution.
Requirements: HARN-01, HARN-02, HARN-03, HARN-04, HARN-05, HARN-06, INFRA-01, INFRA-04
**UI hint**: no
Success criteria:
1. `docker-compose up` brings up Postgres+pgvector, Redis, MinIO locally.
2. A hand-coded graph runs Research→Assemble with one BAML/Pydantic-AI typed cell; invalid model output is parser-repaired or fails on a code path (never flows downstream).
3. A simulated side effect runs exactly once across a forced crash/retry (idempotency key + unique constraint proven by test).
4. The router selects auto/human-review/regenerate from a confidence input via pure code at temp-0.

### Phase 2: Eval spine & gold set
Goal: Eval-on-every-change runs against a real gold set with calibration gates.
Requirements: EVAL-01, EVAL-02, EVAL-03, KNOW-01
**UI hint**: no
Success criteria:
1. A 30–200 example gold set exists for the first engine (owner assigned).
2. Inspect AI suite runs in CI and fails the build on accuracy/F1 regression.
3. DeepEval reports ECE/F1 against the gold set; thresholds gate promotion.
4. pgvector KB scaffolding stores examples + metrics, queryable by tenant.

### Phase 3: First vertical slice (posting, mock tooling)
Goal: One engine produces a real, validated post draft that lands in the review queue (mock MCP).
Requirements: POST-01, POST-02, RSCH-01, KNOW-02
**UI hint**: no
Success criteria:
1. Research→strategy→create produces a post draft grounded in brand-voice from the KB.
2. Media/format validation rejects out-of-spec creatives in code.
3. Basic Exa/Firecrawl research runs under a budget cap.
4. The produced action is persisted with a confidence score, ready for the console.

### Phase 4: Console + API wiring
Goal: The locked Operator Console runs on live GraphQL + SSE for the slice (mocks replaced).
Requirements: API-01, API-02, API-03, API-04, CON-01, CON-02, CON-03, CON-04, CON-05
**UI hint**: yes
Success criteria:
1. NestJS code-first GraphQL serves overview/reviewQueue/action/runs/feed; mutations approve/edit/regenerate/reject work end-to-end.
2. The FastAPI engine streams AG-UI-shaped SSE frames; NestJS `@Sse()` relays them.
3. The console's Review queue shows a real action with its autonomy-decision card; Approve resumes the engine and the action leaves the queue.
4. Live feed + Overview KPIs update in real time over SSE.

### Phase 5: Autonomy engine
Goal: Jury + calibrated confidence + gates + safety route actions auto vs review.
Requirements: AUTON-01, AUTON-02, AUTON-03, AUTON-04, AUTON-05
**UI hint**: no
Success criteria:
1. A cross-family jury scores brand-voice/safety/appropriateness; aggregation is deterministic code.
2. The confidence computer (self-consistency) + calibrated thresholds drive routing; calibrated on the gold set (ECE ≤ 0.05 target).
3. Deterministic gates + an independent safety classifier can veto/escalate.
4. The per-channel autonomy dial changes auto vs approve-first behavior, visible in the console.

### Phase 6: Real tooling & deliverability
Goal: Real IG/FB publishing + Gmail deliverability via exactly-once side effects.
Requirements: POST-03, POST-04, MAIL-03, INFRA-02, INFRA-03
**UI hint**: no
Success criteria:
1. Meta MCP / Graph API publishes a real post idempotently (no double-post under retry); Meta app review started day 1.
2. Scheduled posting fires on a per-client cadence with rate caps honored.
3. Gmail deliverability QA enforces SPF/DKIM/DMARC + one-click unsubscribe + complaint/bounce ceilings.
4. Cloudflare tunnel exposes the portal + Meta webhook; MCP servers + secrets are configured.

### Phase 7: Remaining engines, research & knowledge
Goal: Outreach + engagement engines + shared research + feedback/memory all live.
Requirements: MAIL-01, MAIL-02, MAIL-04, ENG-01, ENG-02, ENG-03, RSCH-02, KNOW-03, KNOW-04
**UI hint**: no
Success criteria:
1. Outreach: intake/dedupe/suppression → per-prospect research → voiced draft → capped send + follow-up.
2. Engagement: deduped webhook → history-aware triage → gated reply with human-like jitter + DM 24h rules.
3. Competitor/winning-pattern mining feeds the strategist with deterministic scoring.
4. Feedback loop writes outcomes back to the KB with drift control.

### Phase 8: Harden & scale
Goal: Reliability + red-team + latency/cost budgets met; ready beyond smoke test.
Requirements: EVAL-04, EVAL-05
**UI hint**: no
Success criteria:
1. Reliability scorecard (consistency/robustness/calibration/safety) gates promotion per autonomy dial.
2. OWASP-Top-10-Agentic red-team suite runs clean.
3. Measurable targets met: brand-voice ≥90% (κ≥0.6), classify P/R ≥0.95, ECE ≤0.05, validator ≥99%, email complaints <0.10%.
4. Per-run and per-reply p95 latency + $/post and $/1k-comments budgets defined and within ceiling.

---
*Last updated: 2026-06-28 after initialization*
