"""The REAL research agent (slice 3) — orchestration, honesty gate, citations.

DB-free + network-free: a fake provider stands in for Firecrawl and the cells run
on deterministic FunctionModels (conftest.tool_model / error_model), so these
assert the agent's CONTRACT — derive <=3 queries, collect real hits, persist them,
synthesize findings that cite only real source URLs — and the HONESTY GATE: an
empty/erroring Firecrawl run degrades to status=failed with NO fabricated sources.
"""

from __future__ import annotations

import pytest

from research.agent import (
    ResearchFindings,
    ResearchQueries,
    build_research_findings_cell,
    build_research_queries_cell,
    run_research,
)
from research.providers.firecrawl import SearchResult
from tests.conftest import error_model, tool_model


class _FakeProvider:
    """Stands in for FirecrawlProvider.search — returns scripted hits per query."""

    def __init__(self, by_query=None, default=None, raise_for=None):
        self._by_query = by_query or {}
        self._default = default if default is not None else []
        self._raise_for = raise_for or {}
        self.calls = []

    def search(self, query, *, limit=5):
        self.calls.append((query, limit))
        if query in self._raise_for:
            raise self._raise_for[query]
        return self._by_query.get(query, self._default)


def _queries_cell(queries):
    return build_research_queries_cell(model=tool_model({"queries": queries}))


def _findings_cell(findings, cited_urls):
    return build_research_findings_cell(
        model=tool_model({"findings": findings, "cited_urls": cited_urls})
    )


def _persist_capture():
    captured = {}

    def persist(*, run_id, tenant_id, sources, dsn=None):
        captured["run_id"] = run_id
        captured["tenant_id"] = tenant_id
        captured["sources"] = sources
        return [f"id-{i}" for i, _ in enumerate(sources)]

    return persist, captured


_HIT_A = SearchResult(url="https://studio-a.test/aftercare", title="Aftercare A", snippet="snip a")
_HIT_B = SearchResult(url="https://studio-b.test/trends", title="Trends B", snippet="snip b")


# ── happy path: real queries -> real hits -> persisted -> cited synthesis ────


def test_happy_path_persists_real_sources_and_cites_them():
    provider = _FakeProvider(default=[_HIT_A, _HIT_B])
    persist, captured = _persist_capture()
    out = run_research(
        "inkhaven",
        "Spring fine-line booking push",
        "run-1",
        provider=provider,
        queries_cell=_queries_cell(["fine line aftercare", "tattoo spring trends"]),
        findings_cell=_findings_cell(
            "Clients want gentle aftercare and fine-line styles are trending.",
            ["https://studio-a.test/aftercare", "https://studio-b.test/trends"],
        ),
        persist=persist,
        ensure=lambda dsn: None,
    )
    assert out.status == "ok"
    assert out.queries == ["fine line aftercare", "tattoo spring trends"]
    # both queries searched, real hits collected from both
    assert provider.calls == [("fine line aftercare", 5), ("tattoo spring trends", 5)]
    assert {s["url"] for s in out.sources} == {
        "https://studio-a.test/aftercare", "https://studio-b.test/trends",
    }
    # persisted exactly the real sources
    assert captured["run_id"] == "run-1"
    assert len(captured["sources"]) == len(out.sources)
    # findings + every cited url is a REAL source url; model pin captured
    assert out.findings
    assert set(out.cited_urls) <= {s["url"] for s in out.sources}
    assert out.cited_urls
    assert isinstance(out.model, str) and out.model


def test_queries_capped_at_three():
    provider = _FakeProvider(default=[_HIT_A])
    out = run_research(
        "inkhaven", "brief", "run-cap",
        provider=provider,
        queries_cell=_queries_cell(["q1", "q2", "q3", "q4", "q5"]),
        findings_cell=_findings_cell("f", ["https://studio-a.test/aftercare"]),
        persist=_persist_capture()[0],
        ensure=lambda dsn: None,
    )
    assert out.queries == ["q1", "q2", "q3"]
    assert len(provider.calls) == 3


# ── HONESTY GATE: no real sources -> failed, empty, nothing persisted/synthesized ─


def test_empty_firecrawl_degrades_to_failed_no_fabrication():
    provider = _FakeProvider(default=[])  # every query returns nothing
    persist, captured = _persist_capture()
    out = run_research(
        "inkhaven", "brief", "run-empty",
        provider=provider,
        queries_cell=_queries_cell(["q1", "q2"]),
        # a findings cell that would fabricate if ever called — it must NOT be called
        findings_cell=_findings_cell("FABRICATED", ["https://fake.test/x"]),
        persist=persist,
        ensure=lambda dsn: None,
    )
    assert out.status == "failed"
    assert out.sources == []
    assert out.findings is None
    assert out.cited_urls == []
    assert out.model is None
    assert "no usable sources" in (out.error or "")
    assert "sources" not in captured  # persist never called


def test_search_errors_recorded_and_degrade_honestly():
    provider = _FakeProvider(raise_for={"q1": RuntimeError("rate limit")})
    out = run_research(
        "inkhaven", "brief", "run-err",
        provider=provider,
        queries_cell=_queries_cell(["q1"]),
        findings_cell=_findings_cell("f", ["x"]),
        persist=_persist_capture()[0],
        ensure=lambda dsn: None,
    )
    assert out.status == "failed"
    assert out.sources == []
    assert any("rate limit" in n for n in out.notes)


def test_no_firecrawl_key_degrades_to_failed():
    # provider auto-build path with no key available -> honest empty, no network
    out = run_research(
        "inkhaven", "brief", "run-nokey",
        api_key="",  # explicit empty -> treated as missing
        queries_cell=_queries_cell(["q1"]),
        findings_cell=_findings_cell("f", ["x"]),
        persist=_persist_capture()[0],
        ensure=lambda dsn: None,
    )
    assert out.status == "failed"
    assert "FIRECRAWL_API_KEY" in (out.error or "")
    assert out.sources == []


# ── citation honesty: hallucinated URLs are dropped ──────────────────────────


def test_hallucinated_citation_is_dropped():
    provider = _FakeProvider(default=[_HIT_A])
    out = run_research(
        "inkhaven", "brief", "run-hallu",
        provider=provider,
        queries_cell=_queries_cell(["q1"]),
        # model cites a url that is NOT in the real source set
        findings_cell=_findings_cell("f", ["https://hallucinated.test/made-up"]),
        persist=_persist_capture()[0],
        ensure=lambda dsn: None,
    )
    assert out.status == "ok"
    # the invented url is dropped; cited falls back to the REAL source set
    assert "https://hallucinated.test/made-up" not in out.cited_urls
    assert out.cited_urls == ["https://studio-a.test/aftercare"]


def test_synthesis_failure_keeps_real_sources():
    provider = _FakeProvider(default=[_HIT_A])
    persist, captured = _persist_capture()
    out = run_research(
        "inkhaven", "brief", "run-synthfail",
        provider=provider,
        queries_cell=_queries_cell(["q1"]),
        findings_cell=build_research_findings_cell(model=error_model(RuntimeError("synth boom"))),
        persist=persist,
        ensure=lambda dsn: None,
    )
    # sources are real + persisted; findings degrade honestly to None, status ok
    assert out.status == "ok"
    assert len(captured["sources"]) == 1
    assert out.findings is None
    assert out.cited_urls == ["https://studio-a.test/aftercare"]
    assert any("synthesis failed" in n for n in out.notes)


# ── schema sanity ────────────────────────────────────────────────────────────


def test_schemas_round_trip():
    assert ResearchQueries(queries=["a"]).queries == ["a"]
    f = ResearchFindings(findings="x", cited_urls=["u"])
    assert f.findings == "x" and f.cited_urls == ["u"]
