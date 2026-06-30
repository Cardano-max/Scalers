"""Dynamic Campaign Workflow Library — Phase A (registry + selection harness).

Workflow-as-DATA, not workflow-as-model-output. A campaign TYPE is a typed,
versioned :class:`~archetypes.spec.ArchetypeSpec` ROW; the LangGraph spine SHAPE is
fixed and code-reviewed; the model only fills CONTENT and emits a bounded,
Enum-validated label. See ``docs/specs/dynamic-campaign-workflows.md`` (§1-§3, §6).

Public surface:
  * :mod:`archetypes.spec`     — ArchetypeSpec + StepKind/TriggerClass/Channel enums.
  * :mod:`archetypes.registry` — the 3 anchor rows + archetype_specs table seed.
  * :mod:`archetypes.router`   — pure-code route_archetype() over pre-declared nodes.
  * :mod:`archetypes.classify` — Haiku classifier brief -> registered archetype id.
"""

from archetypes.spec import (
    ArchetypeSpec,
    Channel,
    GateSet,
    StepKind,
    SubgraphRef,
    TriggerClass,
)

__all__ = [
    "ArchetypeSpec",
    "Channel",
    "GateSet",
    "StepKind",
    "SubgraphRef",
    "TriggerClass",
]
