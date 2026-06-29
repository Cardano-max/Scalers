"""Autonomous marketing TEAM spine (P1).

Three pieces:

* :mod:`team.registry` — names every role and points it at its cell/package
  (new cells on this branch + references to existing P0 cells; honest provenance).
* :mod:`team.orchestrator` — a LangGraph skeleton sequencing the roles
  (plan -> research -> strategy -> draft-many -> critique -> queue). Sends stay
  held: the terminal action is queue-only. Content nodes are explicit TODOs.
* :mod:`team.store` — the durable team tables (agent_runs / assets /
  asset_critiques), additive CREATE TABLE IF NOT EXISTS.
"""

from team.registry import (
    PIPELINE_ORDER,
    ROLE_REGISTRY,
    Role,
    RoleNotWired,
    RoleSpec,
    build,
    get_spec,
)
from team.store import TEAM_DDL, TeamStore

__all__ = [
    "PIPELINE_ORDER",
    "ROLE_REGISTRY",
    "Role",
    "RoleNotWired",
    "RoleSpec",
    "build",
    "get_spec",
    "TEAM_DDL",
    "TeamStore",
]
