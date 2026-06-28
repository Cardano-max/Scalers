# Practitioner-wisdom KB — the GLOBAL grounding partition (bead 1mk.9)

Verbatim practitioner insights + DO/DON'T rules that the writing cells
(brand-voice **S2**, copywriter **S5**) retrieve as few-shot grounding, so
generated content anchors on **authentic human phrasing** and avoids AI tells.

## The rule: VERBATIM is the asset

Operator insight (load-bearing): **re-wording reintroduces AI tells — the exact
human sentence IS the value.** Nothing here is paraphrased. `text` is stored,
embedded, and retrieved byte-for-byte as written (original typos/grammar kept).
`build_practitioner_wisdom.py` *mechanically lifts* every quote out of the source
docs and a provenance check asserts each `text` is a literal substring of its
source — the build fails if a sentence ever drifts.

## Files

| Path | What |
|------|------|
| `sources/winning-strategies-kb.md` | R&D's verbatim harvest — practitioner quotes, de-duped + categorized (general / brand-voice / hooks-cta / research / reply / outreach). |
| `sources/skills-dos-donts.md` | DO / DON'T rules distilled from those quotes (`kind=distilled-rule`). |
| `build_practitioner_wisdom.py` | Generator: parses the sources → `practitioner_wisdom.jsonl`, with a verbatim provenance gate. |
| `practitioner_wisdom.jsonl` | The built asset — one global, source-attributed, categorized row per line. |

## Row shape

```json
{
  "id": "pw-brand-voice-…", "partition": "practitioner-wisdom", "scope": "GLOBAL",
  "category": "brand-voice", "kind": "testimonial",
  "text": "…EXACT human sentence…", "language": "en",
  "source": {"author": "…", "thread": "T2", "subreddit": "r/AskMarketing", "doc": "winning-strategies-kb.md", "attribution_raw": "…"},
  "content_hash": "sha256(text)", "harvested_at": "2026-06-28"
}
```

`kind` distinguishes raw human phrasing from guidance so retrieval can prefer it:
`testimonial` (first-person field quote) · `curated-skill-description` (a list
author's one-line skill summary) · `operator-note` (our own framing) ·
`distilled-rule` (a DO/DON'T rule).

## Rebuild + load

```bash
# from engine/
python -m kb.corpus.build_practitioner_wisdom            # rebuild JSONL (+ provenance gate)
python -m kb.corpus.build_practitioner_wisdom --verify   # provenance check only

# load into the GLOBAL partition (idempotent; needs the rvy.2 KB tables +
# infra/initdb/04-grounding-kb.sql applied)
python -m kb.load_practitioner_wisdom --dsn "$ENGINE_DATABASE_URL"
```

Retrieval (what the cells call): `GroundingStore(dsn).retrieve(query, category="brand-voice", k=5)`.

## Niche note

The harvest is general / B2B / SaaS / DevRel marketing — **no tattoo-specific
content**. The human *phrasing* is the asset; channels + ICP are not. When a
cell grounds on these, the patterns must be retargeted to tattoo-native sources
and the per-artist voice (see `applicability`).

## Provenance / IP

Stored as an INTERNAL grounding reference (attributed, fair-use snippets), never
republished verbatim as our content — generated output is original, grounded on
patterns. Date-stamped (`harvested_at`) so stale advice can be revisited;
contradictory advice is kept (both rows) for the brain/operator to weigh.
