"""Scalers Growth Engine — deterministic control core (`engine/harness/`).

Implements the Phase-1 control-core interfaces from ``docs/systemdesign.md``
§6.2: the ``Node`` protocol, the hand-built ``Harness`` over the LangGraph
spine, the durable ``CompiledGraph`` (``run`` / ``resume`` for HITL), and the
pure-code ``route`` function. The graph topology is fixed in code; the LLM runs
only inside bounded, typed cells and never decides the next step. Models are
pinned and decision/classify cells run at temperature 0.
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
