"""Phase-3 content-engine research adapter (bead a9m.2, RSCH-01).

Pluggable Exa/Firecrawl + Foreplay-primary/Meta-fallback (Reddit OUT) behind a
hard per-run budget cap, MOCK mode by default, returning a typed scored
``ResearchResult`` for the idea/angle cells. Reuses the 1mk.4 ``SourceProvider``
foundation + the ``research.safety`` go-live gate.
"""

from research.content.adapter import EXCLUDED_CHANNELS, ResearchAdapter
from research.content.budget import Budget, BudgetMeter
from research.content.items import (
    ItemKind,
    Mode,
    ResearchItem,
    ResearchResult,
    ScoreBreakdown,
)
from research.content.mock import (
    MockBackend,
    build_adapter,
    live_providers,
    mock_providers,
)

__all__ = [
    "ResearchAdapter",
    "EXCLUDED_CHANNELS",
    "Budget",
    "BudgetMeter",
    "Mode",
    "ScoreBreakdown",
    "ResearchItem",
    "ResearchResult",
    "ItemKind",
    "MockBackend",
    "build_adapter",
    "mock_providers",
    "live_providers",
]
