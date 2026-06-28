# Gold sets

Per-engine gold examples in the `gold_example` shape (docs/eval/labeling-protocol.md
§2): `id, tenant, engine, input, label_payload, rubric_dimension, rater_id(s),
label_version, split (train|holdout|smoke), created_at`. Floor: **≥30% hard
cases** (absolute ≥10).

| File | Bead | Engine | Split | What it gates |
|---|---|---|---|---|
| `research-niche-smoke.jsonl` | `1mk.4` | RESEARCH | smoke | The research adapter's niche-fit, thin-data, and competitor false-positive behavior (replayed by `engine/tests/test_research_gold_smoke.py`). |
| `outreach-smoke.jsonl` | `1mk.7` | OUTREACH | smoke | Suppression-first, deliverability, 4-touch sequence, hard-stop, over-personalization, and the 439 safety hold (replayed by `engine/tests/test_outreach_gold_smoke.py`). |

**Smoke ≠ holdout.** Smoke sets prove deterministic behavior; the real
relevance/recall + calibration holdouts land with the Phase-2 `rvy` gold-set beads
(rvy.7 Inspect suite + rvy.8 calibration). Registry eval-gate for these skills
stays `PENDING-on-gold-set` until those run.
