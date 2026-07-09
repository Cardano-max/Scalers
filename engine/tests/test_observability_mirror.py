"""Self-check for the Langfuse mirror wiring (Part A).

Proves — WITHOUT real keys and WITHOUT a live Langfuse server — that:

* the host is resolved from LANGFUSE_HOST *or* LANGFUSE_BASE_URL (the SDK's own
  arg name), HOST winning;
* the deep-link trace id matches what the v3/v4 SDK derives from a seed;
* :func:`observability.mirror_run` drives the correct SDK calls under BOTH the v2
  (``trace``/``span``) and the v3/v4 (``start_observation``) APIs — emitting one
  trace per run with one span per agent step (carrying node/model/input/output) —
  and flushes;
* it is raise-never and a clean no-op when unconfigured.

The hermetic tests mock the SDK transport. A final test exercises the REAL
langfuse SDK (skipped if not installed) with placeholder keys and an in-memory
OpenTelemetry exporter, asserting the exact spans that WOULD be POSTed to Langfuse
the moment real keys are provided.
"""

from __future__ import annotations

import hashlib

import pytest

import observability as obs


# --------------------------------------------------------------------------- #
# host() — accept either env-var spelling
# --------------------------------------------------------------------------- #
def test_host_prefers_langfuse_host():
    env = {"LANGFUSE_HOST": "https://cloud.langfuse.com", "LANGFUSE_BASE_URL": "http://x"}
    assert obs.host(env) == "https://cloud.langfuse.com"


def test_host_falls_back_to_base_url():
    env = {"LANGFUSE_BASE_URL": "https://us.cloud.langfuse.com"}
    assert obs.host(env) == "https://us.cloud.langfuse.com"


def test_host_default_when_unset():
    assert obs.host({}) == obs._DEFAULT_HOST


# --------------------------------------------------------------------------- #
# trace id + url
# --------------------------------------------------------------------------- #
def test_trace_id_matches_sdk_derivation():
    # langfuse's Langfuse.create_trace_id(seed=x) == sha256(x)[:32]; we replicate
    # it offline so the deep link points at the trace mirror_run actually emits.
    run_id = "team-camp_64b774f6f5b4-9c2d4cb0596e"
    assert obs.trace_id_for(run_id) == hashlib.sha256(run_id.encode()).hexdigest()[:32]
    assert len(obs.trace_id_for(run_id)) == 32


def test_trace_url_none_when_unconfigured():
    assert obs.trace_url("run-1", env={}) is None


def test_trace_url_when_configured():
    env = {
        "LANGFUSE_PUBLIC_KEY": "pk",
        "LANGFUSE_SECRET_KEY": "sk",
        "LANGFUSE_HOST": "https://cloud.langfuse.com",
    }
    url = obs.trace_url("run-1", env=env)
    assert url == f"https://cloud.langfuse.com/traces/{obs.trace_id_for('run-1')}"


# --------------------------------------------------------------------------- #
# mirror_run — unconfigured / raise-never
# --------------------------------------------------------------------------- #
def test_mirror_run_noop_when_no_client(monkeypatch):
    monkeypatch.setattr(obs, "get_langfuse", lambda: None)
    assert obs.mirror_run("run", "tenant", [_span("strategist")]) is False


def test_mirror_run_never_raises_on_sdk_error():
    class Boom:
        def start_observation(self, **_):
            raise RuntimeError("transport down")

    # Even though the SDK raises, mirror_run swallows and reports False.
    assert obs.mirror_run("run", "tenant", [_span("strategist")], client=Boom()) is False


# --------------------------------------------------------------------------- #
# mirror_run — v3/v4 SDK shape (start_observation)
# --------------------------------------------------------------------------- #
class _V4Obs:
    def __init__(self, sink: list, **kwargs):
        self.kwargs = kwargs
        self.ended = False
        self._sink = sink
        sink.append(self)

    def start_observation(self, **kwargs):
        return _V4Obs(self._sink, **kwargs)

    def end(self):
        self.ended = True


class _V4Client:
    def __init__(self):
        self.observations: list[_V4Obs] = []
        self.flushed = False

    def start_observation(self, **kwargs):
        return _V4Obs(self.observations, **kwargs)

    def flush(self):
        self.flushed = True


def test_mirror_run_v4_emits_root_and_per_agent_spans():
    client = _V4Client()
    spans = [
        _span("strategist", model="anthropic:claude-sonnet-4-6", inp="brief", out="positioning"),
        _span("draft", model="anthropic:claude-sonnet-4-6", inp="strategy", out="caption"),
        _span("jury", model="anthropic:claude-opus-4-8", inp="drafts", out="review"),
    ]
    ok = obs.mirror_run("team-camp_x", "ladies8391", spans, run_type="campaign", client=client)

    assert ok is True
    assert client.flushed is True
    names = [o.kwargs.get("name") for o in client.observations]
    # root + 3 agents
    assert names == ["campaign", "strategist", "draft", "jury"]
    assert all(o.ended for o in client.observations)

    # The strategist span carries the real model + captured input/output, and a
    # model-backed step is recorded as a generation (so Langfuse shows the model).
    strat = next(o for o in client.observations if o.kwargs.get("name") == "strategist")
    assert strat.kwargs["as_type"] == "generation"
    assert strat.kwargs["input"] == "brief"
    assert strat.kwargs["output"] == "positioning"
    assert strat.kwargs["metadata"]["model"] == "anthropic:claude-sonnet-4-6"

    # Root carries tenant/run linkage as metadata.
    root = client.observations[0]
    assert root.kwargs["metadata"]["tenant_id"] == "ladies8391"
    assert root.kwargs["metadata"]["run_id"] == "team-camp_x"


def test_mirror_run_v4_structural_step_is_plain_span():
    client = _V4Client()
    ok = obs.mirror_run("run", "t", [_span("queue", model=None)], client=client)
    assert ok is True
    child = client.observations[1]
    assert child.kwargs["as_type"] == "span"  # no model -> not a generation


# --------------------------------------------------------------------------- #
# mirror_run — v2 SDK shape (trace -> span); back-compat
# --------------------------------------------------------------------------- #
class _V2Span:
    def __init__(self, sink: list, **kwargs):
        self.kwargs = kwargs
        sink.append(self)

    def span(self, **kwargs):
        return _V2Span(self.kwargs.setdefault("_children", []), **kwargs)


class _V2Trace:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.spans: list[_V2Span] = []

    def span(self, **kwargs):
        return _V2Span(self.spans, **kwargs)


class _V2Client:
    """A v2-shaped client: exposes ``trace`` but NOT ``start_observation``."""

    def __init__(self):
        self.trace_obj: _V2Trace | None = None
        self.flushed = False

    def trace(self, **kwargs):
        self.trace_obj = _V2Trace(**kwargs)
        return self.trace_obj

    def flush(self):
        self.flushed = True


def test_mirror_run_v2_back_compat():
    client = _V2Client()
    spans = [_span("strategist", model="anthropic:claude-sonnet-4-6", inp="b", out="p")]
    ok = obs.mirror_run("run-2", "tenant", spans, run_type="campaign", client=client)

    assert ok is True
    assert client.flushed is True
    assert client.trace_obj is not None
    assert client.trace_obj.kwargs["id"] == "run-2"
    assert client.trace_obj.kwargs["metadata"]["tenant_id"] == "tenant"
    assert [s.kwargs["name"] for s in client.trace_obj.spans] == ["strategist"]
    assert client.trace_obj.spans[0].kwargs["input"] == "b"


# --------------------------------------------------------------------------- #
# REAL SDK proof — placeholder keys, in-memory OTel exporter, no network
# --------------------------------------------------------------------------- #
def test_real_sdk_exports_trace_with_placeholder_keys():
    """With the real langfuse SDK + placeholder keys, the exact spans that would
    POST to Langfuse are produced. Skips cleanly if the optional SDK is absent."""
    pytest.importorskip("langfuse")
    from langfuse import Langfuse
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    exporter = InMemorySpanExporter()
    client = Langfuse(
        public_key="pk-lf-placeholder",
        secret_key="sk-lf-placeholder",
        host="https://cloud.langfuse.com",
        tracing_enabled=True,
        span_exporter=exporter,
        flush_at=1,
    )

    spans = [
        _span("strategist", model="anthropic:claude-sonnet-4-6", inp="the brief", out="positioning"),
        _span("jury", model="anthropic:claude-opus-4-8", inp="drafts", out="review"),
    ]
    ok = obs.mirror_run("team-camp_real", "ladies8391", spans, run_type="campaign", client=client)
    client.flush()

    assert ok is True
    finished = {s.name: s for s in exporter.get_finished_spans()}
    # root run span + one span per agent
    assert "campaign" in finished
    assert "strategist" in finished
    assert "jury" in finished
    strat = finished["strategist"]
    assert strat.attributes.get("langfuse.observation.input") == "the brief"
    assert strat.attributes.get("langfuse.observation.output") == "positioning"
    assert strat.attributes.get("langfuse.observation.metadata.model") == "anthropic:claude-sonnet-4-6"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _span(node: str, *, model: str | None = None, inp=None, out=None):
    """A minimal duck-typed span (the real harness Span exposes the same attrs)."""
    from harness.spans import Span

    return Span(
        span_id="sp_" + node,
        run_id="run",
        node=node,
        kind="node",
        start_ts="t0",
        input=inp,
        output=out,
        model=model,
        status="ok",
    )
