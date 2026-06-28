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

---


Scalers Growth Engine — the deterministic control core (FastAPI + LangGraph).

**Harness law:** the graph topology is fixed in code and the LLM runs only
inside bounded, typed cells. It never decides the next step. Routing and
scoring are pure Python; models are pinned and decision/classify cells run at
temperature 0.

## Phase 1 (HARN-01/05/06) — control core

Implements the `engine/harness/` interfaces from `docs/systemdesign.md` §6.2.

| Module | Responsibility |
|--------|----------------|
| `harness/config.py` | Pinned model versions + temperature-0 enforcement (HARN-06) |
| `harness/state.py`  | `GraphState` (Pydantic) + `Node` protocol + `Gate` / `Decision` / `RouteDecision` / `AutonomyMode` |
| `harness/router.py` | Pure-code `route(confidence, threshold, gates, autonomy)` → auto/review/regenerate (HARN-05) |
| `harness/nodes.py`  | Deterministic Research and Assemble cells + typed-cell seam |
| `harness/graph.py`  | `Harness` (add_node/add_edge/add_conditional/compile) + `CompiledGraph` (run/resume/astream) + checkpointer factory (HARN-01) |
| `main.py`           | **Thin** FastAPI portal: `/healthz` + webhook ingress + SSE out (NOT the engine) |

### §6.2 interfaces

```python
class Node(Protocol):
    name: str
    async def __call__(self, state: GraphState) -> GraphState: ...

class Harness:
    def add_node(self, node: Node) -> None: ...
    def add_edge(self, src: str, dst: str) -> None: ...
    def add_conditional(self, src: str, choose: Callable[[GraphState], str]) -> None: ...
    def compile(self, checkpointer: BaseCheckpointSaver) -> CompiledGraph: ...

class CompiledGraph:
    async def run(self, run_id: str, init: GraphState) -> GraphState: ...
    async def resume(self, run_id: str, decision: Decision) -> GraphState: ...  # HITL

def route(confidence: float, threshold: float, gates: list[Gate],
          autonomy: AutonomyMode) -> RouteDecision: ...  # "auto"|"review"|"regenerate"
```

`resume()` continues a graph paused at a LangGraph `interrupt()` (human-in-the-loop).

### Pinned models (`docs/stack-decision.md`)

- Opus — `claude-opus-4-8` (hardest writing/judging)
- Sonnet — `claude-sonnet-4-6` (balanced default)
- Haiku — `claude-haiku-4-5` (cheap classification/triage)

### Router

`route` is a pure function. Decision order (first match wins): any failed gate →
`regenerate` (deterministic reject; re-run) → `confidence < threshold` → `review`
→ autonomy `REVIEW` → `review` → otherwise `auto`. Default `threshold = 0.85`
(inclusive auto bar). Regenerate is gate-driven; confidence vs the threshold
splits auto from review.

### Checkpointer

`make_checkpointer()` returns LangGraph's durable `PostgresSaver` when
`ENGINE_DATABASE_URL` is set (the operator's durable-substrate decision;
resumable runs), and an in-memory checkpointer otherwise — so the demo and tests
run with no external dependency. The Postgres dependency is imported lazily.

## Develop

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[test]"   # add ",postgres" for the durable checkpointer
.venv/Scripts/python -m pytest          # test
.venv/Scripts/python -m uvicorn main:app --reload   # run
```

### Thin portal

FastAPI is **not** the engine — the LangGraph StateGraph is. The portal exposes
only liveness, webhook ingress, and SSE egress; the SSE endpoint forwards the
graph's own event stream (it adds no control logic).

```bash
curl localhost:8000/healthz
# webhook ingress (acknowledge + hand off):
curl -X POST localhost:8000/webhooks/meta \
  -H 'content-type: application/json' \
  -d '{"topic": "cold email outreach", "thread_id": "demo-1"}'
# SSE out — relays the LangGraph run frame-by-frame:
curl -N 'localhost:8000/runs/stream?topic=cold%20email%20outreach&thread_id=demo-1'
```

The SSE stream emits one `node` frame per node (Research → Assemble) and a final
`decision` frame from the pure-code router.
