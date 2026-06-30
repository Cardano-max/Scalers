"""Langfuse wiring for the Scalers engine (self-hosted OR Langfuse Cloud).

Per the stack decision, Langfuse is the traces + evals backend. This module is the
single entry point the engine uses to obtain a Langfuse client and to mirror a
run's spans to it.

Design rules that keep tests + CI hermetic:

* It NEVER raises on import — the ``langfuse`` SDK is an optional dependency
  (``pip install -e ".[observability]"``), so a guarded import is used.
* ``get_langfuse()`` returns ``None`` when Langfuse is not configured (no keys in
  the environment) or the SDK is not installed. Callers degrade gracefully:
  observability is best-effort and must never block a run.
* ``mirror_run()`` NEVER raises and is a clean no-op when unconfigured.

Configuration is read from the environment (see ``infra/.env.example`` and the
operator's ``langfuse-keys-to-fill.env``):

* ``LANGFUSE_PUBLIC_KEY``  — e.g. ``pk-lf-...``
* ``LANGFUSE_SECRET_KEY``  — e.g. ``sk-lf-...``
* ``LANGFUSE_HOST``        — region endpoint. EU: ``https://cloud.langfuse.com``;
  US: ``https://us.cloud.langfuse.com``; self-hosted: ``http://localhost:3000``.
  ``LANGFUSE_BASE_URL`` is accepted as a synonym (the Langfuse SDK itself names
  the constructor arg ``base_url``); if both are set, ``LANGFUSE_HOST`` wins.

SDK-version independence
------------------------
The installed Langfuse SDK may be v2 (low-level ``client.trace()`` →
``trace.span()``) or v3/v4 (OTel-based ``client.start_observation()``). The two
APIs are mutually exclusive, so :func:`mirror_run` capability-detects which one
the resolved SDK exposes and emits per-agent spans accordingly. This is what makes
the wiring "work the moment keys are provided" regardless of which langfuse the
operator's environment pins (our ``uv.lock`` currently resolves langfuse 4.12).
"""

from __future__ import annotations

import hashlib
import os
from functools import lru_cache
from typing import Any

_REQUIRED_ENV = ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")
_DEFAULT_HOST = "http://localhost:3000"


def is_configured(env: dict[str, str] | None = None) -> bool:
    """Return True only when every required Langfuse key is set + non-empty."""
    source = os.environ if env is None else env
    return all(source.get(key) for key in _REQUIRED_ENV)


def host(env: dict[str, str] | None = None) -> str:
    """Resolve the Langfuse endpoint, accepting either env-var spelling.

    ``LANGFUSE_HOST`` is the canonical name the engine documents; ``LANGFUSE_BASE_URL``
    is accepted as a synonym (it is what the Langfuse SDK constructor calls the
    argument, so operators copying SDK snippets sometimes use it). ``LANGFUSE_HOST``
    wins if both are present. Falls back to the self-hosted default.
    """
    source = os.environ if env is None else env
    return (
        source.get("LANGFUSE_HOST")
        or source.get("LANGFUSE_BASE_URL")
        or _DEFAULT_HOST
    )


def trace_id_for(run_id: str) -> str:
    """Deterministic Langfuse trace id for a run (``sha256(run_id)[:32]``).

    Langfuse v3/v4 require a 32-char-hex (W3C) trace id, so the SDK derives one
    from a seed via ``Langfuse.create_trace_id(seed=...)``; that derivation is
    ``sha256(seed).hexdigest()[:32]``, replicated here so the engine can compute
    the same id offline (no SDK import) for deep-links. Under the v2 SDK the trace
    id IS the run_id; :func:`mirror_run` seeds the v4 trace with this same value so
    the link and the emitted trace always agree.
    """
    return hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:32]


def trace_url(run_id: str, env: dict[str, str] | None = None) -> str | None:
    """Best-effort deep link to a run's Langfuse trace, or ``None`` if unconfigured.

    Returns ``None`` when Langfuse is not configured so the UI shows no (dead) link.
    The link is host-relative (``{host}/traces/{trace_id}``); it points at the real
    trace id that :func:`mirror_run` emits under v3/v4. It is intentionally
    best-effort — the full cloud URL also embeds a project id we do not resolve
    offline — and never blocks or raises.
    """
    if not is_configured(env):
        return None
    base = host(env).rstrip("/")
    return f"{base}/traces/{trace_id_for(run_id)}"


@lru_cache(maxsize=1)
def get_langfuse() -> Any | None:
    """Return a configured Langfuse client, or ``None`` if unavailable.

    Returns ``None`` (rather than raising) when either the SDK is not installed
    or the required environment variables are absent, so the engine, tests, and
    CI all run without a live Langfuse server.
    """
    if not is_configured():
        return None

    try:
        from langfuse import Langfuse  # type: ignore import-not-found
    except ImportError:
        return None

    try:
        # ``host=`` is accepted by both the v2 and the v3/v4 constructors.
        return Langfuse(
            public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
            secret_key=os.environ["LANGFUSE_SECRET_KEY"],
            host=host(),
        )
    except Exception:
        # A malformed config must never crash the engine; observe-or-nothing.
        return None


def mirror_run(
    run_id: str,
    tenant_id: str,
    spans: list[Any],
    *,
    run_type: str | None = None,
    client: Any | None = None,
) -> bool:
    """Best-effort mirror of a run's spans to Langfuse (per the rvy.1 ADR).

    Returns ``True`` if the spans were sent (handed to the SDK + flushed),
    ``False`` if Langfuse is unconfigured/unreachable or the SDK raised.
    **Never raises** — observability is best-effort and must never block or fail a
    run; the authoritative store is the Postgres run store.

    Emits one trace per run (``run_id``) with one child span per agent step
    (strategist / draft / critic / jury …), carrying that step's node, model,
    input and output so the operator can see what each agent thought. Works under
    both the v2 (``trace``/``span``) and v3/v4 (``start_observation``) SDK APIs.
    """

    lf = client if client is not None else get_langfuse()
    if lf is None:
        return False
    try:
        if hasattr(lf, "start_observation"):
            _mirror_v3plus(lf, run_id, tenant_id, spans, run_type)
        elif hasattr(lf, "trace"):
            _mirror_v2(lf, run_id, tenant_id, spans, run_type)
        else:
            return False
        flush = getattr(lf, "flush", None)
        if callable(flush):
            flush()
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# v3/v4 SDK path — OTel-based start_observation() nesting
# --------------------------------------------------------------------------- #
def _mirror_v3plus(
    lf: Any, run_id: str, tenant_id: str, spans: list[Any], run_type: str | None
) -> None:
    """Emit the run as a v3/v4 trace: a root observation with one child per step."""

    trace_id = trace_id_for(run_id)
    root = lf.start_observation(
        trace_context={"trace_id": trace_id},
        name=run_type or "run",
        as_type="span",
        metadata={"tenant_id": tenant_id, "run_id": run_id},
    )
    try:
        for sp in spans:
            _emit_v3_span(root, sp)
    finally:
        _end(root)


def _emit_v3_span(parent: Any, sp: Any) -> None:
    """Emit one span (and its children) under ``parent`` via the v3/v4 API.

    A model-backed step (``model`` set) becomes a ``generation`` observation so the
    model id renders as the model on the Langfuse generation; structural steps are
    plain ``span`` observations.
    """
    model = getattr(sp, "model", None)
    child = parent.start_observation(
        name=getattr(sp, "node", "span"),
        as_type="generation" if model else "span",
        input=getattr(sp, "input", None),
        output=getattr(sp, "output", None),
        metadata={
            "kind": getattr(sp, "kind", None),
            "status": getattr(sp, "status", None),
            "model": model,
            "duration_ms": getattr(sp, "duration_ms", None),
            "error": getattr(sp, "error", None),
        },
    )
    for grandchild in getattr(sp, "children", []) or []:
        _emit_v3_span(child, grandchild)
    _end(child)


def _end(observation: Any) -> None:
    end = getattr(observation, "end", None)
    if callable(end):
        end()


# --------------------------------------------------------------------------- #
# v2 SDK path — legacy client.trace() -> trace.span()
# --------------------------------------------------------------------------- #
def _mirror_v2(
    lf: Any, run_id: str, tenant_id: str, spans: list[Any], run_type: str | None
) -> None:
    trace = lf.trace(
        id=run_id, name=run_type or "run", metadata={"tenant_id": tenant_id}
    )
    for sp in spans:
        _mirror_span_v2(trace, sp)


def _mirror_span_v2(parent: Any, sp: Any) -> None:
    """Recursively mirror one span (and its children) as v2 Langfuse spans."""

    child = parent.span(
        name=getattr(sp, "node", "span"),
        input=getattr(sp, "input", None),
        output=getattr(sp, "output", None),
        metadata={
            "kind": getattr(sp, "kind", None),
            "status": getattr(sp, "status", None),
            "model": getattr(sp, "model", None),
            "duration_ms": getattr(sp, "duration_ms", None),
            "error": getattr(sp, "error", None),
        },
    )
    for grandchild in getattr(sp, "children", []) or []:
        _mirror_span_v2(child, grandchild)
