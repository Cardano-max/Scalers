"""Pure-code archetype router (§3.2) — the safety rule, in code.

``route_archetype(state)`` is a deterministic function: it reads
``state["archetype_id"]``, looks up the registered :class:`ArchetypeSpec`, and
returns the name of the NEXT pre-declared spine node, selecting ONLY among nodes
that already exist in the compiled graph. Optional spine segments (B2 research, B7
fan-out, B12 durable-wait) are TOGGLED by the spec's ``steps_enabled`` — the
function never names a node that is not in :data:`SPINE_NODES`.

The model NEVER picks topology. Its only structural output is a *classification*
(``classify.py``), Enum-validated against the registry. This module is what turns
that bounded label into a route among fixed nodes.

Verbatim design rule (LangChain): conditional routing "select[s] among predefined
paths, not to invent new ones."
"""

from __future__ import annotations

from typing import Any, Mapping

from archetypes import registry
from archetypes.spec import ArchetypeSpec, StepKind

# The ONLY node names this router is allowed to return. They mirror the compiled
# team-spine nodes (team.orchestrator.build_team_graph). A returned value is
# asserted to be in this set, so a bug (or a tampered spec) can never route to a
# node that does not exist — the graph shape is frozen.
SPINE_NODES: frozenset[str] = frozenset({
    "plan", "research", "strategy", "draft_dispatch", "draft_one",
    "critique", "route", "queue",
})

# Map each toggle-able block to the spine node that realizes it. A block NOT in a
# spec's steps_enabled is skipped (the router routes past it to the next enabled
# node). Order matters: this is the canonical forward sequence of the spine.
_SEQUENCE: tuple[tuple[StepKind | None, str], ...] = (
    (None,                  "plan"),           # always
    (StepKind.B2_ENRICH,    "research"),       # toggle: research only if B2 enabled
    (StepKind.B6_STRATEGY,  "strategy"),       # toggle: strategy (B6)
    (StepKind.B7_DRAFT_MANY, "draft_dispatch"),# toggle: fan-out dispatch (B7)
    (StepKind.B8_CRITIQUE,  "critique"),       # toggle: critic (B8)
    (None,                  "route"),          # always (B9 jury + B10 code route)
    (None,                  "queue"),          # always (B11 HELD enqueue)
)


def _archetype_id_of(state: Any) -> str | None:
    """Read ``archetype_id`` from either a Mapping or a pydantic/attr state object."""
    if isinstance(state, Mapping):
        return state.get("archetype_id")
    return getattr(state, "archetype_id", None)


def _spec_of(state: Any) -> ArchetypeSpec:
    """Resolve the spec for ``state['archetype_id']`` (raises if unregistered)."""
    archetype_id = _archetype_id_of(state)
    if archetype_id is None:
        raise ValueError("route_archetype: state has no 'archetype_id'")
    return registry.get(archetype_id)


def enabled_path(spec: ArchetypeSpec) -> list[str]:
    """The ordered list of spine nodes this spec actually visits.

    Pure data -> data. ``plan``, ``route`` and ``queue`` are always present; the
    toggle-able middle nodes appear only when their block is in ``steps_enabled``.
    ``draft_dispatch`` (the Send fan-out) implies its ``draft_one`` worker.
    """
    path: list[str] = []
    for step, node in _SEQUENCE:
        if step is None or spec.enabled(step):
            path.append(node)
            if node == "draft_dispatch":
                path.append("draft_one")
    # Invariant: never name a node outside the frozen spine.
    assert set(path) <= SPINE_NODES, f"route escaped the frozen spine: {path}"
    return path


def route_archetype(state: Any, *, after: str) -> str:
    """Return the next pre-declared node to run AFTER node ``after``, for this type.

    This is the function fed to ``add_conditional_edges`` for each toggle-point. It
    selects the next ENABLED node in the canonical sequence — skipping any
    toggled-off block — and asserts the result is a real spine node. The model has
    no say here; only the typed spec drives it.
    """
    spec = _spec_of(state)
    path = enabled_path(spec)
    if after not in path:
        # `after` is a node the spec skipped or an unknown node — defensive: fall to
        # the next always-on node so a misuse can never escape the frozen spine.
        raise ValueError(f"route_archetype: node {after!r} not in path for {spec.id!r}: {path}")
    idx = path.index(after)
    if idx + 1 >= len(path):
        return "__end__"  # LangGraph END sentinel handled by the caller
    nxt = path[idx + 1]
    assert nxt in SPINE_NODES, f"route_archetype produced non-spine node {nxt!r}"
    return nxt


def selects_only_predeclared_nodes() -> bool:
    """Self-test used by tests + the honesty gate: for EVERY registered spec, every
    routed node is in the frozen :data:`SPINE_NODES`. Proves the model cannot add
    a node by construction."""
    for spec in registry.REGISTRY.values():
        for node in enabled_path(spec):
            if node not in SPINE_NODES:
                return False
    return True
