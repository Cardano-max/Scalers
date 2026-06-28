# engine

Python core for the Scalers engine (Control + Intelligence + Knowledge).

## `cells/` — typed LLM cells (HARN-02)

The only place an LLM runs. Every call goes through a `Cell[TOut]` that sends a
JSON schema with the prompt, validates the response, repairs missing /
mistyped / validator-failing output by retrying, and returns a **validated
Pydantic object — or raises `CellError`**. Raw model text never flows
downstream (systemdesign §6.3). Built on Pydantic-AI 2.0 (stack-decision.md;
not BAML).

```python
from cells.content_brief import build_content_brief_cell

cell = build_content_brief_cell()           # pinned model, temperature 0
brief = await cell.run("spring booking push for the tattoo studio")
#   -> ContentBrief (typed) or raises CellError after the repair budget
print(cell.metrics.render())                 # first-pass + after-retry valid rates
```

Pieces:

| Module | Role |
|--------|------|
| `cells/base.py` | `Cell[TOut]` framework: structured + text output, repair loop, fail-on-code-path |
| `cells/validators.py` | `Validator` protocol + `ValidatorBank` + built-in checks (length, banned phrase, placeholder, …) |
| `cells/repair.py` | `extract_json` — salvage JSON from markdown / chain-of-thought / sign-off noise |
| `cells/metrics.py` | `ValidRateReport` — first-pass vs after-retry valid rates |
| `cells/content_brief.py` | Example cell: `ContentBrief` |

"Repair or fail" has two surfaces: Pydantic-AI repairs missing/mistyped schema
fields and re-prompts; the deterministic validator bank raises `ModelRetry` on
any `ERROR` issue. When the retry budget is spent, Pydantic-AI raises
`UnexpectedModelBehavior`, which the cell turns into `CellError`.

## Setup & tests

Uses [uv](https://docs.astral.sh/uv/).

```bash
uv sync          # install deps (pydantic-ai-slim[anthropic]==2.0.0, pytest)
uv run pytest    # run the suite (no network / API key needed — tests inject models)
```

Production runs need `ANTHROPIC_API_KEY`; tests drive cells with Pydantic-AI's
`FunctionModel`, so the suite is fully offline and deterministic.
