"""The REAL research agent (P0 make-real / slice 3).

Turns the campaign "research" step from a STUB into a real research agent that
does real web research and persists real, citable sources, then feeds its
findings forward into the strategy step (research -> strategy -> draft).

Flow (all real, no theater):

1. **Derive queries** — a typed LLM cell reads the campaign brief and proposes a
   few (capped at 3) concrete web search queries. WHAT to search.
2. **Search** — each query runs through the EXISTING vetted
   :class:`~research.providers.firecrawl.FirecrawlProvider` ``/v1/search`` (the
   official API: SSRF-guarded, official-API-only, pinned-IP TLS, rate-limited,
   key-from-pack). WHERE it searched + HOW.
3. **Collect + persist** — the real hits (title/url/snippet) are persisted to
   ``research_sources`` via :mod:`research.sources_store`. The citable evidence.
4. **Synthesize** — a second typed LLM cell synthesizes findings that CITE only
   those real source URLs.

HONESTY GATE (absolute): every source URL comes from a real Firecrawl response.
If Firecrawl returns nothing or errors (rate limit, no key, network), the step
degrades to ``status="failed"`` with empty sources and an honest reason — it
NEVER fabricates a source to look populated. Synthesis is also barred from
citing a URL that is not in the real source set (hallucinated URLs are dropped).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from cells.base import Cell
from config.loader import describe_tenant
from cells.validators import (
    ValidatorBank,
    max_items,
    no_placeholder,
    non_empty,
)
from research.providers.firecrawl import FirecrawlProvider
from research import sources_store

# Human-readable description of HOW the research was done — surfaced in the
# research span input so the console can show the method, not just the result.
METHOD = (
    "LLM-derived search queries (<=3) -> Firecrawl official /v1/search API "
    "(SSRF-guarded, official-API-only, pinned-IP TLS, rate-limited, key-from-pack) "
    "-> real results (title/url/snippet) persisted to research_sources -> LLM "
    "synthesis citing only those real source URLs"
)

_MAX_QUERIES = 3
_PER_QUERY_LIMIT = 5


# ── Typed cell schemas ────────────────────────────────────────────────────────


class ResearchQueries(BaseModel):
    """The concrete web search queries derived from a campaign brief."""

    queries: list[str] = Field(
        default_factory=list,
        description=(
            "2-3 concrete, specific web search queries that would surface real, "
            "current evidence for this campaign (audience pain, trends, what "
            "competitors do). Each a short search-engine query, not a sentence."
        ),
    )


class ResearchFindings(BaseModel):
    """A short synthesis grounded ONLY in the real sources, plus the cited URLs."""

    findings: str = Field(
        description=(
            "A 2-4 sentence synthesis of what the provided sources actually say, "
            "useful for shaping the campaign. Grounded ONLY in the sources — no "
            "outside facts, no invented claims."
        )
    )
    cited_urls: list[str] = Field(
        default_factory=list,
        description="The source URLs the findings actually draw on (verbatim from the provided list).",
    )


# ── Cells ─────────────────────────────────────────────────────────────────────


_QUERIES_INSTRUCTIONS = (
    "You are a marketing research analyst. Given a campaign brief, propose the few "
    "best web search queries (at most 3) that would surface real, current evidence "
    "to ground the campaign — audience pain points, current trends, and what "
    "competitors are doing. Each query must be a concrete search-engine query "
    "(keywords), specific to this brand/niche — no generic boilerplate, no placeholders."
)

_FINDINGS_INSTRUCTIONS = (
    "You are a marketing research analyst. You are given a campaign brief and a list "
    "of REAL web search results (each with a title, URL, and snippet). Synthesize a "
    "short findings summary (2-4 sentences) of what these sources actually say that "
    "is useful for the campaign. Ground every statement ONLY in the provided "
    "sources — do not add outside facts and do not invent anything. Then list the "
    "URLs you actually drew on in cited_urls, copied verbatim from the provided list."
)


def _queries_validators() -> ValidatorBank:
    return ValidatorBank(validators=(non_empty("queries"), max_items("queries", _MAX_QUERIES)))


def _findings_validators() -> ValidatorBank:
    return ValidatorBank(
        validators=(
            non_empty("findings"),
            no_placeholder("findings"),
            non_empty("cited_urls"),
        )
    )


def build_research_queries_cell(**overrides) -> Cell[ResearchQueries]:
    """Build the query-derivation cell (pinned ``anthropic:claude-haiku-4-5`` default)."""
    params = dict(
        name="research-queries",
        schema=ResearchQueries,
        instructions=_QUERIES_INSTRUCTIONS,
        validators=_queries_validators(),
    )
    params.update(overrides)
    return Cell(**params)


def build_research_findings_cell(**overrides) -> Cell[ResearchFindings]:
    """Build the findings-synthesis cell (pinned ``anthropic:claude-haiku-4-5`` default)."""
    params = dict(
        name="research-findings",
        schema=ResearchFindings,
        instructions=_FINDINGS_INSTRUCTIONS,
        validators=_findings_validators(),
    )
    params.update(overrides)
    return Cell(**params)


def build_queries_prompt(descriptor: str, brief: str) -> str:
    """Render the prompt the query-derivation cell runs against.

    ``descriptor`` is the REQUIRED, honest account-identity line from
    :func:`config.loader.describe_tenant` — it replaces the old hardcoded
    ``"a women-led tattoo studio"`` literal so a tenant's identity is never
    fabricated. Callers resolve it with ``describe_tenant(tenant_id)``.
    """
    return (
        f"Studio/account: {descriptor}.\n"
        f"Campaign brief: {brief}\n"
        "Propose the few best web search queries (at most 3) that would surface "
        "real, current evidence to ground THIS campaign. Be specific to this studio "
        "and niche — concrete search keywords, no generic boilerplate."
    )


def build_findings_prompt(descriptor: str, brief: str, sources: list[dict]) -> str:
    """Render the synthesis prompt from the brief + the REAL collected sources.

    ``descriptor`` is the REQUIRED honest account-identity line from
    :func:`config.loader.describe_tenant` (never a fabricated niche).
    """
    lines = [
        f"Studio/account: {descriptor}.",
        f"Campaign brief: {brief}",
        "",
        "Real web search results to synthesize from (cite by URL):",
    ]
    for i, s in enumerate(sources, 1):
        title = s.get("title") or "(no title)"
        snippet = s.get("snippet") or ""
        lines.append(f"[{i}] {title}\n    URL: {s.get('url')}\n    Snippet: {snippet}")
    lines.append(
        "\nSynthesize a 2-4 sentence findings summary grounded ONLY in these "
        "sources, then list the URLs you drew on in cited_urls (verbatim)."
    )
    return "\n".join(lines)


# ── Outcome + orchestration ───────────────────────────────────────────────────


@dataclass
class ResearchOutcome:
    """The result of the research step — real or honestly-empty, never faked."""

    run_id: str
    queries: list[str]
    method: str
    sources: list[dict]          # [{query, url, title, snippet}] — real, persisted
    findings: str | None         # synthesis grounded in the sources (None if not captured)
    cited_urls: list[str]        # subset of real source URLs (never invented)
    model: str | None            # the real synthesis model pin (None if no synthesis ran)
    status: str                  # "ok" (real sources persisted) | "failed" (honest empty)
    error: str | None
    notes: list[str] = field(default_factory=list)


def run_research(
    tenant_id: str,
    brief: str,
    run_id: str,
    *,
    dsn: str | None = None,
    api_key: str | None = None,
    enabled: bool | None = None,
    provider=None,
    queries_cell: Cell | None = None,
    findings_cell: Cell | None = None,
    persist=None,
    ensure=None,
    per_query_limit: int = _PER_QUERY_LIMIT,
    max_queries: int = _MAX_QUERIES,
) -> ResearchOutcome:
    """Run the REAL research step end to end and return a :class:`ResearchOutcome`.

    Injectables (``provider`` / ``queries_cell`` / ``findings_cell`` / ``persist`` /
    ``ensure``) let tests drive it with a fake provider + deterministic models and
    no DB/network; production wires the real Firecrawl provider + Postgres store.

    HONESTY GATE: ``status="failed"`` with empty ``sources`` whenever Firecrawl
    returns nothing or errors — never a fabricated source. ``status="ok"`` requires
    at least one REAL persisted source.
    """
    notes: list[str] = []
    # Honest account-identity for every research prompt — from the tenant's REAL pack,
    # or the bare handle when no pack is on file (never a fabricated niche).
    descriptor = describe_tenant(tenant_id)

    # 1. Derive the real search queries (real LLM cell).
    qcell = queries_cell or build_research_queries_cell()
    try:
        plan = qcell.run_sync(build_queries_prompt(descriptor, brief))
        queries = [q.strip() for q in plan.queries if q and q.strip()][:max_queries]
    except Exception as exc:  # noqa: BLE001 — honest fail, no fabricated queries
        return ResearchOutcome(
            run_id, [], METHOD, [], None, [], None, "failed",
            f"query derivation failed: {type(exc).__name__}: {exc}", notes,
        )
    if not queries:
        return ResearchOutcome(
            run_id, [], METHOD, [], None, [], None, "failed",
            "no search queries derived from brief", notes,
        )

    # 2. The provider: the EXISTING vetted Firecrawl, gated enabled + key-from-pack.
    if provider is None:
        key = api_key if api_key is not None else os.environ.get("FIRECRAWL_API_KEY")
        if not key:
            return ResearchOutcome(
                run_id, queries, METHOD, [], None, [], None, "failed",
                "no FIRECRAWL_API_KEY — research degraded to honest-empty (no fabricated sources)",
                notes,
            )
        provider = FirecrawlProvider(
            api_key=key, enabled=(True if enabled is None else enabled)
        )

    # 3. Run each query through the real search, collect REAL results.
    sources: list[dict] = []
    for q in queries:
        try:
            hits = provider.search(q, limit=per_query_limit)
        except Exception as exc:  # noqa: BLE001 — record the real reason, keep going
            notes.append(f"search failed for {q!r}: {type(exc).__name__}: {exc}")
            continue
        for h in hits:
            sources.append({"query": q, "url": h.url, "title": h.title, "snippet": h.snippet})

    # HONESTY GATE: no real sources -> fail honestly. Persist nothing, synth nothing.
    if not sources:
        reason = "Firecrawl returned no usable sources"
        if notes:
            reason += " (" + "; ".join(notes) + ")"
        return ResearchOutcome(
            run_id, queries, METHOD, [], None, [], None, "failed", reason, notes
        )

    # 4. Persist the REAL sources (citable evidence).
    persist_fn = persist or sources_store.record_sources
    ensure_fn = sources_store.ensure_schema if ensure is None else ensure
    try:
        if ensure_fn:
            ensure_fn(dsn)
        persist_fn(run_id=run_id, tenant_id=tenant_id, sources=sources, dsn=dsn)
    except Exception as exc:  # noqa: BLE001 — sources are real but unpersisted; be honest
        notes.append(f"persist failed: {type(exc).__name__}: {exc}")
        return ResearchOutcome(
            run_id, queries, METHOD, sources, None, [], None, "failed",
            f"persist failed: {type(exc).__name__}: {exc}", notes,
        )

    # 5. Synthesize findings citing ONLY the real sources.
    real_urls = [s["url"] for s in sources]
    real_url_set = set(real_urls)
    fcell = findings_cell or build_research_findings_cell()
    model = str(fcell.model)
    findings: str | None = None
    cited: list[str] = []
    try:
        synth = fcell.run_sync(build_findings_prompt(descriptor, brief, sources))
        findings = synth.findings.strip()
        # Drop any URL the model cited that is not in the REAL source set (no
        # hallucinated citations). If none survive, cite the real set itself —
        # the findings are grounded in it and every URL is real.
        cited = [u for u in synth.cited_urls if u in real_url_set]
        if not cited:
            cited = list(real_urls)
    except Exception as exc:  # noqa: BLE001 — sources real+persisted; degrade findings honestly
        notes.append(f"synthesis failed: {type(exc).__name__}: {exc}")
        findings = None
        cited = list(real_urls)

    return ResearchOutcome(
        run_id, queries, METHOD, sources, findings, cited, model, "ok", None, notes
    )
