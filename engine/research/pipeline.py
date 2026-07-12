"""Pipeline glue (p3.0-B): run the ResearchRouter against LIVE providers and
persist its real, deduped citations to ``research_sources``.

The :class:`~research.router.ResearchRouter` does the fan-out / merge / dedupe and
does NO I/O itself. This module adds the two ends the router deliberately omits:

  * :func:`live_registry` — build the vetted LIVE provider set from env keys
    (Firecrawl + Exa, gated ``enabled``). A provider with no key is still wired but
    degrades honestly at call time (raises -> router notes -> empty), never faked.
  * :func:`gather_and_persist` — run ``router.gather(query)`` then persist the
    result's ``sources_cited`` (the merged, url-deduped, verbatim provider hits) to
    ``research_sources`` via :mod:`research.sources_store`.

HONESTY GATE: only ``result.sources_cited`` is persisted — every row is a real
provider API hit. An honest-empty / fully-degraded run persists NOTHING.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from research.adapter import ResearchQuery, ResearchResult, SourceProvider
from research.providers.anthropic_research import AnthropicResearchProvider
from research.providers.exa import ExaProvider
from research.providers.firecrawl import FirecrawlProvider
from research.providers.fixture import FixtureProvider
from research.router import ResearchRouter
import research.sources_store as sources_store


def live_registry(
    env: Mapping[str, str] | None = None,
    *,
    include_fixture: bool = False,
) -> dict[str, SourceProvider]:
    """The LIVE vetted provider registry, keyed by pack ``[research].sources`` name.

    Keys come from ``env`` (defaults to ``os.environ``) — ``ANTHROPIC_API_KEY`` /
    ``FIRECRAWL_API_KEY`` / ``EXA_API_KEY``; never a vendored ``.env`` read by the
    provider itself. A provider is ``enabled=True`` only when its key is present
    (so an absent key degrades honestly rather than half-arming a live call).
    ``include_fixture`` adds the offline fixture under the ``fixture`` name (NOT a
    live source) for callers that want a deterministic fallback in the same router.

    The client-directed PRIMARY is ``anthropic`` (PA meeting 2026-07-11): Claude
    web research, armed from ``ANTHROPIC_API_KEY`` — the same key the drafting
    cells already read.
    """
    e = env if env is not None else os.environ
    anthropic_key = e.get("ANTHROPIC_API_KEY") or None
    fc_key = e.get("FIRECRAWL_API_KEY") or None
    exa_key = e.get("EXA_API_KEY") or None
    reg: dict[str, SourceProvider] = {
        "anthropic": AnthropicResearchProvider(
            api_key=anthropic_key, enabled=bool(anthropic_key)
        ),
        "firecrawl": FirecrawlProvider(api_key=fc_key, enabled=bool(fc_key)),
        "exa": ExaProvider(api_key=exa_key, enabled=bool(exa_key)),
    }
    if include_fixture:
        reg["fixture"] = FixtureProvider()
    return reg


def gather_and_persist(
    router: ResearchRouter,
    query: ResearchQuery,
    *,
    run_id: str,
    tenant_id: str,
    dsn: str | None = None,
    persist=None,
    ensure=None,
) -> tuple[ResearchResult, list[str]]:
    """Run the router for ``query`` and persist its real citations; return
    ``(result, persisted_source_ids)``.

    ``result.sources_cited`` is the merged, url-deduped, verbatim provider hits.
    Each is written to ``research_sources`` as ``{query,url,title,snippet}`` tagged
    with ``run_id`` + ``tenant_id``. Injectable ``persist`` / ``ensure`` let tests
    drive it with no DB. HONESTY GATE: nothing to cite -> nothing persisted.
    """
    result = router.gather(query)
    if not result.sources_cited:
        return result, []
    persist_fn = persist or sources_store.record_sources
    ensure_fn = sources_store.ensure_schema if ensure is None else ensure
    if ensure_fn:
        ensure_fn(dsn)
    ids = persist_fn(
        run_id=run_id,
        tenant_id=tenant_id,
        sources=[dict(s) for s in result.sources_cited],
        dsn=dsn,
    )
    return result, ids
