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

## Phase 1 (HARN-01/03/05/06) — control core

Implements the `engine/harness/` interfaces from `docs/systemdesign.md` §6.2 (+ §2.2 / §5.1 durability).

| Module | Responsibility |
|--------|----------------|
| `harness/config.py` | Pinned model versions + temperature-0 enforcement (HARN-06) |
| `harness/state.py`  | `GraphState` (Pydantic) + `Node` protocol + `Gate` / `Decision` / `RouteDecision` / `AutonomyMode` |
| `harness/router.py` | Pure-code `route(confidence, threshold, gates, autonomy)` → auto/review/regenerate (HARN-05) |
| `harness/nodes.py`  | Deterministic Research and Assemble cells + typed-cell seam |
| `harness/graph.py`  | `Harness` (add_node/add_edge/add_conditional/compile) + `CompiledGraph` (run/recover/resume/astream) + durable checkpointer factory + replay guard (HARN-01/03) |
| `harness/serde.py`  | Checkpoint serializer with state types allow-listed (durable, strict-msgpack-safe) |
| `harness/recovery.py` | Bounded 3-level recovery: retry → regenerate → human-review (HARN-03, §2.4) |
| `harness/runstore.py` | Thin `RunStore` (DBOS-swappable) + `InMemory`/`Postgres` impls (JSONB append-only `steps[]`) + query API + `execute_and_record` (HARN-03, §5.1) |
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

### Durability & crash recovery (HARN-03)

`make_checkpointer()` (async) returns LangGraph's durable **`AsyncPostgresSaver`**
over a psycopg `AsyncConnectionPool` when `ENGINE_DATABASE_URL` is set — the
operator's durable-substrate decision — and an in-memory checkpointer otherwise,
so the demo and tests run with no external dependency. The harness drives every
graph via async `ainvoke`/`astream`, so the checkpointer must be async (the sync
`PostgresSaver` raises under the async loop); `get_state`/`is_complete` likewise
use `aget_state`. The Postgres dependency is imported lazily; both checkpointers
use the allow-listed serializer. Verified end to end against a real Postgres via
`tests/test_postgres_integration.py` (skipped unless `ENGINE_DATABASE_URL` is set).

A checkpoint is a **save-point, not exactly-once execution** (§2.2). State is
persisted after every node, so a crashed run resumes from the last completed
node — `CompiledGraph.recover(run_id)` re-runs only the pending node(s); completed
nodes are not re-applied. Re-running a **completed** `run_id` is rejected
(`RunAlreadyCompletedError`) rather than replaying the checkpoint and
re-accumulating append-reduced channels (CustomerAcq-fk5); run-key uniqueness is
also enforced at the durable store. (Exactly-once side effects are a separate
guarantee at the DB boundary — eng3, HARN-04. DBOS Transact stays deferred behind
the `RunStore` interface.)

### Run-state store (§5.1)

`RunStore` is a thin, DBOS-swappable interface over a Postgres `runs` row with a
JSONB **append-only** `steps[]` trajectory, status, and auto/review/retry
counters — the read model the gateway serves for the console Runs/Overview.
`execute_and_record(...)` is the durable run-driver: it runs the graph and
appends a step per node, then finishes with the routed decision. Query via
`get_run` (trajectory) / `list_runs` (history).

## Develop

Uses [uv](https://docs.astral.sh/uv/) (single engine build config).

```bash
uv sync                  # install deps; add: uv sync --extra postgres for the durable checkpointer
uv run pytest            # full engine suite (cells + harness); offline, no API key / DB needed
uv run uvicorn main:app --reload   # run the thin portal
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
