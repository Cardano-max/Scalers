"""Durable checkpoint serializer (HARN-03).

The default LangGraph serializer warns when it serializes our typed state models
("Deserializing unregistered type ... will be blocked in a future version").
For a *durable* checkpointer that's a time-bomb, so we hand both checkpointers a
serializer with our state types explicitly allow-listed. Roundtrips are clean
and the future strict-msgpack mode won't reject our checkpoints.
"""

from __future__ import annotations

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from .state import AssembleOutput, Gate, GraphState, JurySignal, ResearchOutput

# The typed values that may appear in a checkpointed ``GraphState`` channel.
_STATE_TYPES = (GraphState, ResearchOutput, AssembleOutput, Gate, JurySignal)

_ALLOWLIST = [(t.__module__, t.__qualname__) for t in _STATE_TYPES]


def make_serde() -> JsonPlusSerializer:
    """Return the checkpoint serializer with our state types allow-listed."""

    return JsonPlusSerializer(allowed_msgpack_modules=_ALLOWLIST)
