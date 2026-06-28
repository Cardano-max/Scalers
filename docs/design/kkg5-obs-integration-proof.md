# kkg.5 (OBS-05) — Observability integration-proof design

- **Status:** DESIGN (build-ready for eng + qa2; arch-owned). Pure design — no implementation.
- **Owner:** arch · **Bead:** CustomerAcq-kkg.5 (Phase 2.5 obs slice) · **Consumes:** kkg.4 (OBS-04 read API, in progress, eng1)
- **Reads from:** OBS-01 spans (`harness/spans.py` → durable run store), OBS-02 jury/decision (`autonomy/store.py`: `autonomy_decisions` + `autonomy_jury`), OBS-03 deep-link/engagement (`infra/initdb/05-side-effect-capture.sql`)
- **Aligns to:** [`adr/phase-2-eval-spine.md`](../adr/phase-2-eval-spine.md) Decision 6 (Langfuse mirror best-effort; authoritative store gates), the kkg epic AC, and the operator's stated acceptance.

The proof that the **emission + persistence + read** layer works end-to-end **before** the Phase-4 console UI is wired — so the console renders real data on day one instead of the prototype's mock `string[]`/seeded arrays. It is an integration-marked test on **real Postgres** plus a documented demo, exercising a real auto-executed action through OBS-01/02/03 → the OBS-04 (kkg.4) API.

---

## 1. Operator acceptance (what the proof must demonstrate)

Open any auto-executed action and see: **real structured spans** (durations + I/O), the **per-judge jury**, **working deep-links**, **thread/comments**, and in **Runs** a **scoped event history where each event expands to its trace**.

## 2. The fixture — ONE real auto-executed action on real PG

The proof seeds exactly one auto-executed action end-to-end (current mock-tooling + stub-jury producers) so the OBS stores hold real rows, then exercises the kkg.4 contract against it.

> **⚠ Cross-bead requirement (b3f / bead-439): the proof MUST lift the 439 hold for the test tenant to get an *auto* action.** After b3f, the engine is **held-by-default** (`HoldRegistry.is_held` → True unless lifted; `DEFAULT_HOLD_REGISTRY` holds everything), so every action routes to **review**, not auto. To produce the *auto-executed* action the operator acceptance requires, the fixture must run with an **explicitly lifted** hold for the test tenant/channel — `run_slice(..., hold_registry=HoldRegistry().lift(TENANT))` (the same `LIFTED` pattern eng3's b3f tests use). **This is a test-only opt-in, not a production auto-fire** — production stays held until the Phase-5 stack + operator lift. The proof documents this explicitly so "auto-executed action" is not mistaken for "the system auto-fires in prod today" (it does not).

The fixture produces real rows in:
- **OBS-01** — `Span` rows in the durable run store: node spans (the run **trajectory**) + nested cell/gate/tool spans (the **reasoning trace**), each `{span_id, run_id, parent_span_id, node, kind, start/end/duration_ms, input, output, status, error}`.
- **OBS-02** — `autonomy_decisions` (pooled_confidence, threshold, agreement, gates jsonb `[{label,ok}]`, safety_verdict, decision, esc `{kind,label}`) + `autonomy_jury` (**one row per cross-family judge**: judge, family, voice, safety, appr).
- **OBS-03** — side-effect capture (`05-side-effect-capture.sql`): provider deep-link/URL + thread/comments/engagement, keyed to the idempotency key.

## 3. Assertions (per OBS surface, via the kkg.4 read API)

Each assertion = a kkg.4 GraphQL query / SSE event + the field that must resolve to **real** data (not a mock string):

1. **Structured spans w/ duration + I/O** — `Run.trajectory` / Activity thinking-spans return `Span{node, kind, duration_ms > 0, input, output, status, parent_span_id}`; node spans carry nested cell/gate/tool **children** (the reasoning trace). Assert `kind ∈ {node,cell,gate,tool}`, non-null I/O (truncation marker allowed, `MAX_IO_CHARS`), and parent linkage. Not mock `string[]`.
2. **Per-judge jury** — the Approval/Activity jury card returns **one entry per cross-family judge** `{judge, family, voice, safety, appr}` + pooled confidence + per-channel threshold + agreement + gates `[{label,ok}]` + safety_verdict + esc `{kind,label}` (binds to the `autonomy_jury`/`autonomy_decisions` shape).
3. **Working deep-link + engagement** — the action resolves a provider deep-link/URL and thread/comments/engagement (mock-provider URL in Phase 3; real in Phase 6) keyed to its idempotency key.
4. **Runs scoped event history → trace** — the feed/event-history returns events each carrying `run_id`, and **each event expands to its run + full trace** (the operator's headline acceptance). Assert an event resolves to its span tree.
5. **Tenant-scoped** — the same queries for a second tenant return **none** of tenant-A's rows (RLS / tenant filter; no cross-tenant leak).
6. **Authoritative, not Langfuse-dependent** — the read API serves from the **durable store** (authoritative); the proof passes **with Langfuse unconfigured/down** (`observability.get_langfuse()` → None). Tracing is best-effort; the proof must not depend on a live Langfuse (rvy.1 ADR Decision 6).

## 4. Edge-case scenarios (each a documented assertion)

- **Failed run** — a node raises; the trace shows that span `status="failed"` with `error` populated (the console renders it red). The event history still resolves to the (partial) trace.
- **Escalated action** — a held/low-agreement/safety case: jury shows the split/esc reason (`esc{kind,label}` ≠ none); decision = review; the deep-link is absent (nothing fired) but the jury + reasoning trace are present.
- **Paused harness** — no new events/spans are emitted while paused; the read API returns the existing history unchanged (no phantom rows).

## 5. The "real data fills in" path (document, don't block)

The proof runs on the **current** producers; the schema is stable and values sharpen over later phases — call this out so the console (Phase 4) binds once:
- **Deep-links:** mock-provider result URL now (Phase 3 tooling) → **real IG/FB/Gmail URLs** when real MCP tooling lands (Phase 6). Same field; the capture mechanism is proven now with mock results.
- **Jury verdicts:** the stub jury emits uniform per-dimension scores + agreement 1.0 with **no model call** (b3f/critique). The proof asserts the **shape** (per-judge rows, all dimensions, pooled/threshold/agreement/esc) is real and persisted; the **values** become real when the **Phase-5** cross-family jury + computed confidence land — **no schema change** (`autonomy/jury.py` swaps the producer).
- **Confidence / embedder / gold set:** placeholder confidence, SHA-256 embedder, mock gold set today; all Phase-5+. None may gate an auto-fire (439/b3f) — which is exactly why the proof's auto action runs under a **test-only lift**, not a production auto-path.

## 6. Build & verify

- **Location:** the contract assertions run in `gateway/` against real Postgres (per qa2's staged QA plan); the **engine seed** (one lifted-hold auto action end-to-end) produces the rows. Integration-marked (`@pytest.mark.integration` engine-side; the gateway contract test against the pgvector + obs service).
- **Done = green:** the integration test passes on real PG in CI; the "real-data fills in" path is documented (this doc); the demo (open the action → spans/jury/deep-link/event-history→trace) is reproducible.
- **Ownership:** arch designs (this doc); eng builds the seed + the kkg.4 contract test; **qa2 verifies** (their pre-staged AC-by-AC plan on kkg.4 maps 1:1 to §3 here). kkg.5 stays **blocked on kkg.4** (eng1) landing.

## 7. Cross-bead invariants the proof pins (arch notes)

1. **Held-by-default (b3f)**: the auto fixture requires an explicit hold-lift; a **second scenario** asserts the *unlifted* (held) action produces **review + no deep-link/no fire** — proving the obs layer correctly shows a held action's trace+jury without a side effect. This doubles as a b3f regression at the obs layer.
2. **Authoritative store gates, Langfuse observes** (rvy.1 D6): proof green with Langfuse down.
3. **Tenant isolation**: no cross-tenant rows via the read API.
4. **Schema-stable, values-fill-in**: Phase-4 binds to these shapes once; Phase-5/6 sharpen values without a contract change.

---

*Owner: arch. Consumes kkg.4. Aligns to rvy.1 ADR Decision 6 + the kkg epic AC + operator acceptance. Build-ready once kkg.4 lands.*
