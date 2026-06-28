"""Shared test helpers: deterministic model injection.

Cells never call a real LLM in tests. Instead we drive them with Pydantic-AI's
``FunctionModel``, scripting exactly what the "model" returns on each call so we
can exercise valid output, repair-on-retry, persistent failure, and messy text.
"""

from __future__ import annotations

from typing import Any

from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel


def tool_model(*payloads: dict[str, Any]) -> FunctionModel:
    """A model that returns each ``payload`` as an output-tool call, in order.

    Use for structured cells. The Nth call returns ``payloads[N]``; once the
    payloads run out it repeats the last one (so a single bad payload models a
    persistently-broken model).
    """
    calls = {"n": 0}

    def fn(messages, info: AgentInfo) -> ModelResponse:
        idx = min(calls["n"], len(payloads) - 1)
        calls["n"] += 1
        tool_name = info.output_tools[0].name
        return ModelResponse(parts=[ToolCallPart(tool_name, payloads[idx])])

    return FunctionModel(fn)


def text_model(*texts: str) -> FunctionModel:
    """A model that returns each raw ``text`` string, in order (for text-output cells)."""
    calls = {"n": 0}

    def fn(messages, info: AgentInfo) -> ModelResponse:
        idx = min(calls["n"], len(texts) - 1)
        calls["n"] += 1
        return ModelResponse(parts=[TextPart(texts[idx])])

    return FunctionModel(fn)


def error_model(exc: BaseException) -> FunctionModel:
    """A model that raises ``exc`` instead of responding.

    Simulates a non-ModelBehavior failure (network/timeout/connector/etc.) at the
    model boundary so the cell wrapper's error handling can be exercised.
    """

    def fn(messages, info: AgentInfo) -> ModelResponse:
        raise exc

    return FunctionModel(fn)


# A well-formed content brief payload reused across tests.
VALID_BRIEF: dict[str, Any] = {
    "headline": "Bold blackwork sleeve drop",
    "platform": "instagram",
    "angle": "Show the linework process to build trust with new clients",
    "caption": (
        "Three sessions, one sleeve. Swipe to watch the linework come together "
        "and book your chair for spring before the calendar fills."
    ),
    "hashtags": ["blackwork", "tattoo", "linework"],
    "call_to_action": "Book your spring chair",
}
