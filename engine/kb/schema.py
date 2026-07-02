"""Typed shapes for the eval KB (KNOW-01, ADR Decision 1-2).

Plain enums + dataclasses — the per-engine ``label``/``expected`` payloads stay
opaque ``jsonb`` (dict) so the schema is stable while engines differ. DB CHECK
constraints (infra/initdb/03-eval-kb.sql) are the validation backstop.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any


class Engine(str, Enum):
    POSTING = "POSTING"
    OUTREACH = "OUTREACH"
    ENGAGEMENT = "ENGAGEMENT"
    RESEARCH = "RESEARCH"


class Split(str, Enum):
    CALIBRATION = "CALIBRATION"
    HOLDOUT = "HOLDOUT"  # blind set for brand-voice >=90% (never used to tune)
    SMOKE = "SMOKE"
    RUBRIC = "RUBRIC"  # jury/human-rater anchor corpus (4jx.12); never scored as
    # holdout, never feeds a gate (dataset_for filters to CALIBRATION/HOLDOUT)


class Scope(str, Enum):
    TENANT = "TENANT"
    GLOBAL = "GLOBAL"


class Direction(str, Enum):
    GTE = "GTE"  # value must be >= threshold (e.g. precision)
    LTE = "LTE"  # value must be <= threshold (e.g. ECE)


class RunKind(str, Enum):
    PER_COMMIT = "PER_COMMIT"
    PER_PROMOTION = "PER_PROMOTION"


@dataclass(frozen=True)
class GoldExample:
    id: str
    tenant_id: str
    engine: Engine
    cell: str
    input: dict[str, Any]
    expected: dict[str, Any] | None
    rubric_dimensions: list[str]
    split: Split
    label_version: int
    content_hash: str
    created_at: datetime | None = None
    created_by: str | None = None


@dataclass(frozen=True)
class GoldLabel:
    id: str
    example_id: str
    tenant_id: str
    rater_id: str
    dimension: str
    label: dict[str, Any]
    label_version: int
    created_at: datetime | None = None


@dataclass(frozen=True)
class EvalMetric:
    metric: str
    value: float
    scope: Scope = Scope.TENANT
    tenant_id: str | None = None
    engine: str | None = None
    cell: str | None = None
    # The routing channel the metric was measured for (4jx.16): lift is granted
    # per (tenant, channel), so the D5 precondition gates must be queryable at
    # that grain. None for cell-level metrics with no channel dimension.
    channel: str | None = None
    threshold: float | None = None
    direction: Direction | None = None
    run_kind: RunKind | None = None
    label_version: int | None = None
    model_pins_hash: str | None = None
    prompt_version: str | None = None
    dataset_hash: str | None = None
    git_sha: str | None = None
    langfuse_trace_id: str | None = None
    # WHICH confidence producer fed this metric (4jx.17, lift precondition (e)):
    # the LiftController refuses to lift a channel whose gate rows were driven by
    # a stub/jury-only/sc-only path. None for metrics with no confidence input.
    confidence_provenance: str | None = None
    id: str | None = None
    passed: bool | None = None
    created_at: datetime | None = None

    def compute_passed(self) -> bool | None:
        """value ⨝ direction ⨝ threshold -> passed (None if no threshold/dir)."""
        if self.threshold is None or self.direction is None:
            return None
        if self.direction is Direction.GTE:
            return self.value >= self.threshold
        return self.value <= self.threshold
