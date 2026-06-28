"""Pluggable research adapter (bead a9m.2, RSCH-01) — the Phase-3 content brain's
external-signal layer.

One interface over many backends (Exa + Firecrawl for web; Foreplay PRIMARY +
free Meta Ad Library FALLBACK for competitor ads; **Reddit is OUT of the MVP
brain**), under a **hard per-run budget cap**, with **MOCK mode by default** so
the slice + CI run with zero live calls. Returns a typed, normalized, scored
:class:`ResearchResult` the idea/angle cells consume; a dead/over-budget backend
**degrades** (the run continues on the others with a flag) rather than failing.

Reuses the 1mk.4 foundation: the ``SourceProvider`` protocol + provider gather
shapes (Signal/Community/Creative) and the ``research.safety`` network gate. This
layer adds budget accounting, mock/live mode, the Foreplay→Meta fallback rule,
and normalization into scored items.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable

from research.adapter import Channel, Creative, ProviderResult, ResearchQuery, Signal
from research.content.budget import Budget, BudgetMeter
from research.content.items import (
    Mode,
    ResearchItem,
    ResearchResult,
)

# Competitor-ad fallback order (contract §1): Foreplay primary, Meta Ad Library
# fallback. The fallback is only spent if the primary degrades or returns nothing.
_COMPETITOR_PRIMARY = "foreplay"
_COMPETITOR_FALLBACK = "meta_ad_library"

# Per-intent backend order (Reddit OUT — no r_tattoos backend in the MVP brain).
_INTENT_ORDER: dict[str, tuple[str, ...]] = {
    "map_market": ("exa", "firecrawl"),
    "find_communities": ("exa", "firecrawl"),
    "competitor_creatives": (_COMPETITOR_PRIMARY, _COMPETITOR_FALLBACK),
}


def _cost_of(provider, query: ResearchQuery) -> float:
    """Provider credit cost for a query (optional ``cost_estimate``; 0 if absent —
    e.g. the free fixture / Meta Ad Library)."""
    fn = getattr(provider, "cost_estimate", None)
    return float(fn(query)) if callable(fn) else 0.0


class ResearchAdapter:
    """Budget-capped, mock-default, pluggable fan-out over vetted backends."""

    def __init__(
        self,
        providers: Iterable,
        *,
        budget: Budget | None = None,
        mode: Mode = Mode.MOCK,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._providers = {p.name: p for p in providers}
        self._budget = budget or Budget.unlimited()
        self._mode = mode
        self._clock = clock or time.monotonic

    @property
    def provider_names(self) -> tuple[str, ...]:
        return tuple(self._providers)

    def _order(self, query: ResearchQuery) -> list[str]:
        wanted = _INTENT_ORDER.get(query.intent, ("exa", "firecrawl"))
        return [n for n in wanted if n in self._providers]

    def run(self, query: ResearchQuery) -> ResearchResult:
        meter = BudgetMeter(self._budget, start=self._clock())
        items: list[ResearchItem] = []
        used: list[str] = []
        degraded: list[str] = []
        notes: list[str] = []
        over_budget = False
        is_competitor = query.intent == "competitor_creatives"
        primary_satisfied = False

        for name in self._order(query):
            # Fallback rule: skip the Meta fallback if Foreplay already delivered.
            if is_competitor and name == _COMPETITOR_FALLBACK and primary_satisfied:
                notes.append("foreplay satisfied; meta fallback skipped")
                continue

            provider = self._providers[name]
            meter.tick(self._clock())
            cost = _cost_of(provider, query)
            if not meter.can_afford(calls=1, credits=cost):
                over_budget = True
                notes.append(f"budget cap reached before '{name}'")
                break

            try:
                result = provider.gather(query)
            except NotImplementedError:
                degraded.append(name)
                notes.append(f"'{name}' not wired (live client pending)")
                continue
            except Exception as exc:  # a dead/flaky backend degrades, never sinks the run
                degraded.append(name)
                notes.append(f"'{name}' failed: {type(exc).__name__}")
                continue

            meter.charge(calls=1, credits=cost)
            used.append(name)
            new_items = self._normalize(name, result)
            items.extend(new_items)
            if is_competitor and name == _COMPETITOR_PRIMARY and any(
                i.kind == "competitor_creative" for i in new_items
            ):
                primary_satisfied = True

            if meter.exhausted:
                over_budget = True
                notes.append("budget exhausted mid-run")
                break

        ranked = self._dedupe_and_rank(items)[: query.limit]
        if not ranked:
            notes.append("zero results — idea/angle cells fall back to brand context")
        return ResearchResult(
            query_intent=query.intent,
            items=tuple(ranked),
            sources_used=tuple(used),
            over_budget=over_budget,
            degraded=tuple(degraded),
            mode=self._mode,
            notes=tuple(notes),
        )

    # ── normalization (1mk.4 provider shapes -> scored items) ────────────────

    @staticmethod
    def _normalize(source: str, result: ProviderResult) -> list[ResearchItem]:
        out: list[ResearchItem] = []
        for s in result.signals:
            if isinstance(s, Signal) and s.text.strip():
                out.append(ResearchItem(source=source, kind="signal", text=s.text,
                                        score=_clamp(s.confidence), url=s.url, evidence=s.evidence))
        for c in result.communities:
            if getattr(c, "name", "").strip():
                out.append(ResearchItem(source=source, kind="signal",
                                        text=f"community: {c.name} — {c.entry_tactic}",
                                        score=0.4, url=getattr(c, "url", None)))
        for cr in result.creatives:
            if isinstance(cr, Creative) and cr.angle.strip():
                text = cr.angle if not cr.hook else f"{cr.angle} — hook: {cr.hook}"
                out.append(ResearchItem(source=source, kind="competitor_creative", text=text,
                                        score=_clamp(cr.confidence), url=cr.url, evidence=cr.evidence))
        return out

    @staticmethod
    def _dedupe_and_rank(items: list[ResearchItem]) -> list[ResearchItem]:
        best: dict[tuple[str, str], ResearchItem] = {}
        for it in items:
            key = (it.kind, it.text.strip().lower())
            if key not in best or it.score > best[key].score:
                best[key] = it
        return sorted(best.values(), key=lambda i: i.score, reverse=True)


def _clamp(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else float(x)


# Channels that the content brain never sources from (Reddit OUT, contract §1).
EXCLUDED_CHANNELS = frozenset({Channel.R_TATTOOS})
