# engine/kb — eval-spine KB (KNOW-01)

Tenant-isolated pgvector store for the eval spine, built to
[`docs/adr/phase-2-eval-spine.md`](../../../docs/adr/phase-2-eval-spine.md)
Decisions 1–2. It is the single source of truth for "what good looks like"
(gold examples + per-rater labels) and "how we scored last time" (eval metrics).
Generic (the niche lives in per-tenant packs); **offline** — never on the engine
hot path.

## Tables (`infra/initdb/03-eval-kb.sql`, extends the Phase-1 stack)

| Table | Purpose |
|-------|---------|
| `gold_example` | one row per example under test — `tenant_id`, `engine`, `cell`, `input`, `expected`, `rubric_dimensions`, `split`, `label_version`, `embedding vector(384)` |
| `gold_label` | per-rater × per-dimension labels (never collapsed, so κ is computable) |
| `eval_metric` | append-only metric history + the gating source of truth |

All three are `tenant_id`-scoped with **row-level security** (FORCE) as a
defense-in-depth backstop; the production app connects as the non-superuser
`scalers_app` role with `app.current_tenant` set per request.

## Usage

```python
from kb import KbStore, Engine, Split, EvalMetric, Direction, RunKind

kb = KbStore(dsn)  # dsn = ENGINE_DATABASE_URL

# Ingest a gold example (idempotent on its natural key) — embedded via a local model.
eid = kb.upsert_gold_example(
    tenant_id="ink-studio", engine=Engine.POSTING, cell="content_brief",
    input={"topic": "spring promo"}, expected={"on_voice": True},
    rubric_dimensions=["voice"], split=Split.HOLDOUT,
)
kb.add_gold_label(example_id=eid, tenant_id="ink-studio", rater_id="r1",
                  dimension="voice", label={"on_voice": True})

# Fetch a gold set (always tenant-scoped) and write/read metric history.
gold = kb.get_gold_set(tenant_id="ink-studio", engine=Engine.POSTING, label_version=1)
kb.record_metric(EvalMetric(metric="brand_voice_onvoice", value=0.92,
    tenant_id="ink-studio", engine="POSTING", cell="content_brief",
    threshold=0.90, direction=Direction.GTE, run_kind=RunKind.PER_PROMOTION,
    label_version=1))  # `passed` is computed from value⨝direction⨝threshold
history = kb.get_metrics(tenant_id="ink-studio", metric="brand_voice_onvoice")
```

Every read **requires** `tenant_id` (or an explicit `scope=GLOBAL` for metrics) —
the DAL never issues a query that could return cross-tenant rows.

## Embedding

`DeterministicEmbedder` is a dependency-free, reproducible local embedder
(`EMBED_DIM=384`) so the scaffolding + per-commit gate stay hermetic. Swap a real
local model (sentence-transformers `all-MiniLM-L6-v2`, Ollama `nomic-embed-text`)
in behind the `Embedder` protocol for KNOW-02 grounding — the dimension must match
the `vector(384)` column (a mismatch fails loudly on write).

## Tests

```bash
cd infra && docker compose up -d
pytest engine/tests/test_kb_embedding.py            # unit (no DB)
ENGINE_DATABASE_URL=… pytest -m integration engine/tests/test_kb_store.py   # real PG
```
