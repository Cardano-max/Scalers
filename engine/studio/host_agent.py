"""Studio Host agent (P2 interactive Slice 1) — ONE real conversational agent.

This is a real Pydantic-AI call pinned to a real model. Given the conversation so
far it replies conversationally AND asks 1-2 clarifying questions to shape a
campaign brief. There is exactly ONE agent here: the multi-role brainstorm
(Researcher / Strategist / Copywriter / Critic) and any tool/citation grounding
are explicitly OUT of scope for this slice — the system prompt forbids the host
from pretending to run them.

The model is pinned the same way the typed cells pin theirs
(``cells.base.DEFAULT_MODEL``): ``anthropic:claude-haiku-4-5``. Pydantic-AI reads
``ANTHROPIC_API_KEY`` from the environment (loaded from ``engine/.env`` by
``harness.config`` at import). Temperature ~0.4 keeps the host warm and varied
without drifting off-task.
"""

from __future__ import annotations

from collections.abc import Iterable
from functools import lru_cache

from pydantic_ai import Agent
from pydantic_ai.models import KnownModelName

# Pinned, real model (HARN-06 style pin). host turns persist THIS exact string.
HOST_MODEL: KnownModelName = "anthropic:claude-haiku-4-5"
HOST_TEMPERATURE: float = 0.4

_SYSTEM = (
    "You are the Studio Host for a marketing campaign studio. You partner with a "
    "single operator (a tattoo artist or small studio owner) to shape a campaign "
    "BRIEF through conversation.\n"
    "\n"
    "Your job each turn:\n"
    "1. Respond warmly and concretely to what the operator just said.\n"
    "2. Reflect back the campaign details you have gathered so far (goal, "
    "audience, channels, offer/promotion, timing, constraints, brand voice).\n"
    "3. Ask 1-2 SHORT clarifying questions that move the brief forward — pick the "
    "highest-leverage gaps, do not interrogate.\n"
    "\n"
    "Hard rules (honesty):\n"
    "- You are ONE host having a normal conversation. Do NOT role-play as multiple "
    "agents (no 'Researcher says…', no 'Strategist thinks…').\n"
    "- Do NOT claim to browse the web, run tools, pull competitor ads, or cite "
    "sources — none of that is wired yet. If something needs research, say you'll "
    "note it for later rather than inventing facts.\n"
    "- Keep it tight: a few sentences, then your question(s). End your message with "
    "the clarifying question(s) so the operator knows what to answer next."
)


@lru_cache(maxsize=1)
def _agent() -> Agent:
    """Build the host agent once. ``defer_model_check=True`` so importing this
    module never requires a model/key (the key is only needed at run time)."""
    return Agent(
        HOST_MODEL,
        instructions=_SYSTEM,
        model_settings={"temperature": HOST_TEMPERATURE},
        defer_model_check=True,
    )


def _format_transcript(history: Iterable) -> str:
    """Render the conversation so far as a plain transcript the host replies to.

    ``history`` is the full ordered list of turns INCLUDING the operator message
    just sent (the host replies to the last operator line)."""
    lines: list[str] = []
    for turn in history:
        who = "Operator" if turn.role == "operator" else "Studio host"
        lines.append(f"{who}: {turn.text}")
    lines.append("")
    lines.append(
        "Reply now as the Studio host to the operator's latest message. Keep it "
        "short, and end with 1-2 clarifying questions that move the brief forward."
    )
    return "\n".join(lines)


async def run_host(history: Iterable) -> str:
    """Run the real host agent over the transcript and return its reply text.

    Raises whatever Pydantic-AI raises (e.g. missing key, network) — the caller
    surfaces it; nothing is fabricated on failure."""
    prompt = _format_transcript(history)
    result = await _agent().run(prompt)
    return result.output
