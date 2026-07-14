"""Structured trace/span emission (OBS-01).

Every node — and every cell/gate/tool inside a node — emits a structured
``Span`` ``{span_id, run_id, parent_span_id, node, kind, start->end->duration_ms,
input, output, status, error}``. Node spans are the run's **trajectory**
(top-level); cell/gate/tool spans nest as ``children`` (the **reasoning trace**).

Spans are gathered per run by a :class:`SpanCollector` registered under the
``run_id`` (robust cross-task lookup), with a context-var stack giving parent
linkage for nested spans. The harness auto-instruments every node it builds
(``Harness.add_node``); cells/gates/tools wrap their work in :func:`span`.

Persistence is the durable run store (authoritative); Langfuse mirroring is
best-effort (per the rvy.1 ADR — observing never gates).
"""

from __future__ import annotations

import contextvars
import json
import time
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field, model_validator

# Large I/O is truncated (with a marker) so a single span can't bloat the row;
# full-blob offload to MinIO with a stored ref is a later optimisation.
MAX_IO_CHARS = 2000


class Span(BaseModel):
    """One structured trace span. Doubles as the run store's step (back-compat).

    The legacy step fields (``seq``, ``at``, ``text``, ``state``) mirror span
    data so existing Runs/Overview readers keep working: ``state`` = node name,
    ``at`` = start, ``text`` = a short summary.
    """

    span_id: str
    run_id: str
    node: str
    kind: str = "node"  # node | cell | gate | tool
    parent_span_id: str | None = None

    start_ts: str
    end_ts: str | None = None
    duration_ms: float | None = None

    input: str | None = None
    output: str | None = None
    input_truncated: bool = False
    output_truncated: bool = False

    # The pinned model id that produced this span's output, for a model-backed
    # span (e.g. ``"anthropic:claude-haiku-4-5"`` for the draft cell and — under
    # the 2026-07-14 cost order — for every juror too). ``None`` for a
    # structural/stub span where no model was called — never invented. Queryable
    # from ``runs.steps`` as ``s->>'model'``.
    model: str | None = None

    status: str = "ok"  # ok | failed
    error: str | None = None

    children: list[Span] = Field(default_factory=list)

    # legacy/back-compat mirror of the old RunStep shape
    seq: int = 0
    at: str | None = None
    text: str | None = None
    state: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _backfill_legacy(cls, data: Any) -> Any:
        """Read pre-OBS-01 step rows ({seq,at,text,state}) as minimal spans."""

        if isinstance(data, dict):
            data = dict(data)
            data.setdefault("node", data.get("state", ""))
            data.setdefault("start_ts", data.get("at", ""))
            data.setdefault("span_id", "")
            data.setdefault("run_id", "")
        return data


# --- redaction seam (per-tenant PII policy plugs in here) -------------------

Redactor = Callable[[Any], Any]


def _identity(value: Any) -> Any:
    return value


_redactor: Redactor = _identity


def set_redactor(redactor: Redactor | None) -> None:
    """Install a redaction function applied to every span input/output.

    The per-tenant PII policy (engine/config packs) wires its redactor here;
    the default is a no-op. Kept as a single chokepoint so redaction can never
    be bypassed by an individual span.
    """

    global _redactor
    _redactor = redactor or _identity


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, BaseModel):
        return value.model_dump_json()
    try:
        return json.dumps(value, default=str, sort_keys=True)
    except (TypeError, ValueError):
        return str(value)


def _summarize(value: Any) -> tuple[str | None, bool]:
    """Redact then truncate a value for storage; returns (text, truncated)."""

    if value is None:
        return None, False
    text = _to_str(_redactor(value))
    if len(text) > MAX_IO_CHARS:
        return text[:MAX_IO_CHARS] + f"…(+{len(text) - MAX_IO_CHARS} chars)", True
    return text, False


def summarize(value: Any) -> tuple[str | None, bool]:
    """Public redact+truncate summarizer for callers that build spans manually.

    The orchestrator populates the draft/jury node spans with the REAL captured
    cell prompt + model output, so it needs the same redaction/truncation a span
    context manager applies. Returns ``(text, truncated)``; ``None`` in → ``None`` out
    (an honestly-uncaptured value stays null, never a fabricated string).
    """

    return _summarize(value)


# --- per-run span collection ------------------------------------------------


class SpanCollector:
    """Gathers the top-level node spans for one run, in execution order."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.spans: list[Span] = []


_collectors: dict[str, SpanCollector] = {}
_current_span: contextvars.ContextVar[Span | None] = contextvars.ContextVar(
    "current_span", default=None
)


@contextmanager
def collecting(run_id: str):
    """Activate span collection for ``run_id`` for the duration of the block."""

    collector = SpanCollector(run_id)
    _collectors[run_id] = collector
    try:
        yield collector
    finally:
        _collectors.pop(run_id, None)


def collector_for(run_id: str) -> SpanCollector | None:
    return _collectors.get(run_id)


def _attach(span: Span, parent: Span | None, run_id: str) -> None:
    """Attach a finished span to its parent's children, or the run's top level."""

    if parent is not None:
        parent.children.append(span)
        return
    collector = _collectors.get(run_id)
    if collector is not None:
        collector.spans.append(span)


def _new_span(node: str, kind: str, run_id: str, parent: Span | None) -> Span:
    start = _now_iso()
    return Span(
        span_id=uuid.uuid4().hex,
        run_id=run_id,
        node=node,
        kind=kind,
        parent_span_id=parent.span_id if parent else None,
        start_ts=start,
        at=start,
        state=node,
    )


def _finish(span: Span, t0: float) -> None:
    span.end_ts = _now_iso()
    span.duration_ms = (time.perf_counter() - t0) * 1000.0
    span.text = f"{span.node} {span.status}"


def instrument(node: Any) -> Callable[[Any], Any]:
    """Wrap a Node so each call emits a ``node`` span with duration + I/O.

    The wrapper is transparent: it returns the node's channel updates unchanged
    and re-raises any error (so crash recovery is unaffected), recording a
    ``status="failed"`` span with the error first.
    """

    name = getattr(node, "name", getattr(node, "__name__", "node"))

    async def wrapped(state: Any) -> Any:
        run_id = getattr(state, "run_id", "") or ""
        parent = _current_span.get()
        span = _new_span(name, "node", run_id, parent)
        token = _current_span.set(span)
        t0 = time.perf_counter()
        try:
            output = await node(state)
            span.input, span.input_truncated = _summarize(state)
            span.output, span.output_truncated = _summarize(output)
            span.status = "ok"
            return output
        except Exception as exc:
            span.status = "failed"
            span.error = f"{type(exc).__name__}: {exc}"
            span.input, span.input_truncated = _summarize(state)
            raise
        finally:
            _finish(span, t0)
            _current_span.reset(token)
            _attach(span, parent, run_id)

    wrapped.__name__ = name
    return wrapped


@contextmanager
def span(name: str, *, kind: str = "cell", input: Any = None):
    """Emit a nested cell/gate/tool span under the current node span.

    Use inside an instrumented node (``with span("voice-gate", kind="gate"):``).
    The span links to the enclosing node span as a child; on exception it is
    recorded ``status="failed"`` and the error re-raises.
    """

    parent = _current_span.get()
    run_id = parent.run_id if parent else ""
    sp = _new_span(name, kind, run_id, parent)
    if input is not None:
        sp.input, sp.input_truncated = _summarize(input)
    token = _current_span.set(sp)
    t0 = time.perf_counter()
    try:
        yield sp
        if sp.status == "ok":
            sp.status = "ok"
    except Exception as exc:
        sp.status = "failed"
        sp.error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        _finish(sp, t0)
        _current_span.reset(token)
        _attach(sp, parent, run_id)
