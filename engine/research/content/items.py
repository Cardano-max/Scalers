"""Normalized, scored result the idea/angle cells consume (bead a9m.2, contract §5).

The adapter fans out across backends (each returns the 1mk.4 provider shapes —
Signal/Community/Creative) and **normalizes** them into a flat list of scored
:class:`ResearchItem`s, so the strategist/idea cell reads one uniform type
regardless of source. Junk / non-normalizable input is dropped, not poisoned in.

Scoring (arch Decision 1a): a single ``score`` float is the ranking key now; the
optional ``ScoreBreakdown`` sub-object (field ``breakdown``) is RESERVED and left
``None`` so Phase-7 can add weightable dimensions with no breaking change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal


class Mode(str, Enum):
    """MOCK (default) runs from fixtures with zero live calls; LIVE hits the
    official APIs (behind the sec go-live gate). MOCK is auto-selected when keys
    are absent so CI / the dev box never hard-error."""

    MOCK = "mock"
    LIVE = "live"


@dataclass(frozen=True)
class ScoreBreakdown:
    """RESERVED (arch Decision 1a) — Phase-7 weightable dimensions. Optional now
    so adding it later is NOT a breaking contract change. a9m.2 leaves it None."""

    relevance: float | None = None
    recency: float | None = None
    authority: float | None = None


ItemKind = Literal["signal", "angle", "competitor_creative"]


@dataclass(frozen=True)
class ResearchItem:
    """One normalized research finding (the uniform type idea/angle cells read)."""

    source: str                       # backend name (firecrawl/exa/foreplay/…)
    kind: ItemKind
    text: str
    score: float                      # 0..1 single relevance/quality — ranking key
    url: str | None = None
    evidence: tuple[str, ...] = ()
    breakdown: ScoreBreakdown | None = None  # RESERVED (Decision 1a); None in a9m.2


@dataclass(frozen=True)
class ResearchResult:
    """The merged, scored, budget-aware result for one run."""

    query_intent: str
    items: tuple[ResearchItem, ...] = ()
    sources_used: tuple[str, ...] = ()
    over_budget: bool = False
    degraded: tuple[str, ...] = ()    # backends that failed / were skipped
    mode: Mode = Mode.MOCK
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_empty(self) -> bool:
        return not self.items

    def top(self, k: int) -> tuple[ResearchItem, ...]:
        """The k highest-scored items (already sorted by the adapter)."""
        return self.items[:k]
