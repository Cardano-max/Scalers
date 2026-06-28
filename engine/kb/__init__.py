"""Eval-spine KB (KNOW-01, ADR docs/adr/phase-2-eval-spine.md Decisions 1-2).

A tenant-isolated pgvector store for gold examples + their per-rater labels +
the eval-metric history that is the gating source of truth. Generic (the niche
stays in per-tenant packs); offline (never on the engine hot path).
"""

from kb.embedding import EMBED_DIM, DeterministicEmbedder, Embedder
from kb.schema import (
    Direction,
    Engine,
    EvalMetric,
    GoldExample,
    GoldLabel,
    RunKind,
    Scope,
    Split,
)
from kb.store import KbStore, content_hash

__all__ = [
    "KbStore",
    "content_hash",
    "Embedder",
    "DeterministicEmbedder",
    "EMBED_DIM",
    "Engine",
    "Split",
    "Scope",
    "Direction",
    "RunKind",
    "GoldExample",
    "GoldLabel",
    "EvalMetric",
]
