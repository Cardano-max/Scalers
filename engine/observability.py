"""Langfuse (self-hosted) wiring for the Scalers engine.

Per the stack decision, Langfuse is the self-hosted traces + evals backend. This
module is the single entry point the engine uses to obtain a Langfuse client.

Design rules that keep tests + CI hermetic:

* It NEVER raises on import — the ``langfuse`` SDK is an optional dependency
  (``pip install -e ".[observability]"``), so a guarded import is used.
* ``get_langfuse()`` returns ``None`` when Langfuse is not configured (no keys in
  the environment) or the SDK is not installed. Callers degrade gracefully:
  observability is best-effort and must never block a run.

Configuration is read from the environment (see ``infra/.env.example``):

* ``LANGFUSE_PUBLIC_KEY``
* ``LANGFUSE_SECRET_KEY``
* ``LANGFUSE_HOST`` (e.g. ``http://localhost:3000`` for the self-hosted stack)
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

_REQUIRED_ENV = ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")
_DEFAULT_HOST = "http://localhost:3000"


def is_configured(env: dict[str, str] | None = None) -> bool:
    """Return True only when every required Langfuse env var is set + non-empty."""
    source = os.environ if env is None else env
    return all(source.get(key) for key in _REQUIRED_ENV)


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

    return Langfuse(
        public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
        secret_key=os.environ["LANGFUSE_SECRET_KEY"],
        host=os.environ.get("LANGFUSE_HOST", _DEFAULT_HOST),
    )


def mirror_run(
    run_id: str,
    tenant_id: str,
    spans: list[Any],
    *,
    run_type: str | None = None,
    client: Any | None = None,
) -> bool:
    """Best-effort mirror of a run's spans to Langfuse (per the rvy.1 ADR).

    Returns ``True`` if the spans were sent, ``False`` if Langfuse is
    unconfigured/unreachable or the SDK raised. **Never raises** — observability
    is best-effort and must never block or fail a run; the authoritative store
    is the Postgres run store.
    """

    lf = client if client is not None else get_langfuse()
    if lf is None:
        return False
    try:
        trace = lf.trace(
            id=run_id, name=run_type or "run", metadata={"tenant_id": tenant_id}
        )
        for sp in spans:
            _mirror_span(trace, sp)
        flush = getattr(lf, "flush", None)
        if callable(flush):
            flush()
        return True
    except Exception:
        return False


def _mirror_span(parent: Any, sp: Any) -> None:
    """Recursively mirror one span (and its children) as Langfuse spans."""

    child = parent.span(
        name=getattr(sp, "node", "span"),
        input=getattr(sp, "input", None),
        output=getattr(sp, "output", None),
        metadata={
            "kind": getattr(sp, "kind", None),
            "status": getattr(sp, "status", None),
            "duration_ms": getattr(sp, "duration_ms", None),
            "error": getattr(sp, "error", None),
        },
    )
    for grandchild in getattr(sp, "children", []) or []:
        _mirror_span(child, grandchild)
