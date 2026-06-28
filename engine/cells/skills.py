"""Skill composition for cells (Phase-3 ADR Decision 2).

Skills are Anthropic Agent Skills — folders of instructions/examples the engine
*loads* (it does not author them). A cell is parameterized by a :class:`Skill`:
the skill's ``instructions`` are composed into the cell's instruction string and
its ``examples`` augment the prompt. Per-tenant voice vs global authenticity is
expressed by *where the ref comes from* (``pack.voice.skill`` vs the global
``ai-flagger``), not by branching code.

This is the seam the brand-voice (1mk.2) + ai-flagger (1mk.3) skills plug into.
The ``SkillLoader`` protocol lets the real loader (eng) and a test/static loader
share one shape; nothing here reaches the network.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field


class Skill(BaseModel):
    """A loaded Agent Skill: a ref + the instructions/examples it contributes."""

    model_config = {"frozen": True}

    ref: str = Field(description="Skill ref, e.g. 'brand-voice/ink-studio' or 'ai-flagger'.")
    instructions: str = Field(default="", description="Composed into the cell's instructions.")
    examples: tuple[dict, ...] = Field(default_factory=tuple, description="Optional few-shot exemplars.")


@runtime_checkable
class SkillLoader(Protocol):
    """Loads a skill by ref, on demand (the real loader caches; eng-owned)."""

    def load(self, ref: str) -> Skill: ...


class StaticSkillLoader:
    """A loader over an in-memory ``{ref: Skill}`` map — for tests + the offline
    slice (no skill registry / filesystem reach)."""

    def __init__(self, skills: dict[str, Skill] | None = None) -> None:
        self._skills = dict(skills or {})

    def add(self, skill: Skill) -> None:
        self._skills[skill.ref] = skill

    def load(self, ref: str) -> Skill:
        if ref not in self._skills:
            # A missing voice skill is not fatal — the cell still runs on its base
            # instructions (and the slice flags low grounding). Return an empty skill.
            return Skill(ref=ref)
        return self._skills[ref]


def compose_instructions(base: str, *skills: Skill | None) -> str:
    """Prepend each skill's instructions to the cell's base instructions, in
    order. Skills with empty instructions are skipped. Deterministic."""
    parts = [s.instructions.strip() for s in skills if s and s.instructions.strip()]
    parts.append(base.strip())
    return "\n\n".join(parts)
