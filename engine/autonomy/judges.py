"""Real cross-family jury — judge cells + panel + orchestration (AUTON-01 / 4jx.2).

Replaces the always-agree stub with **independent** judges that actually score an
action. Each judge is a typed :class:`~cells.base.Cell` (temp-0, pinned model,
typed-or-raise — no raw text) emitting a :class:`JudgeScore`: per-dimension
``voice``/``safety``/``appr`` in ``[0,1]`` (0–4 rubric anchors normalized) + an
``on_voice`` bool + per-dimension machine-detectable ``hard_fail`` tags.

**Cross-family by construction.** The panel is ≥2 **Claude Opus jurors with varied
prompt framings** (independent readings, temp-0) **+ ≥1 local Ollama juror** as the
out-of-family voice. The client provides only an Anthropic key, so the Ollama juror
(local, no key) is what guarantees cross-family is met even in the only-Anthropic
case — the engine never silently collapses to a single family. GPT/Gemini/DeepSeek
jurors are added only if their keys are configured.

**Orchestration (`run_jury`) honors the ADR edge cases:** a judge that times
out/errors/refuses is **dropped** (absent from the panel — never counted as
agreement, never blocks the run); if **all** judges are unavailable the result is
empty so the decision layer fails safe to review (no confidence → never a passing
default). The actual aggregation (reliability-weighted means, the hard-fail floor,
real per-dimension agreement) lives in :mod:`autonomy.aggregate`.

The live model wiring (the Ollama provider extra; real Opus calls) is gated; the
``judge_runner`` seam lets the whole panel be exercised deterministically with
``FunctionModel`` in tests and swapped for real cells in production.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from autonomy.decision import JudgeVote
from cells.base import Cell

# Weight applied to a judge that responded normally (default uniform; gold-calibrated
# later). A dropped judge contributes NO vote (absent), so it can never be counted as
# agreement — "reduced weight" is realized as reduced panel coverage, caught by the
# degraded check, not by a fabricated score.
DEFAULT_WEIGHT = 1.0


class JudgeScore(BaseModel):
    """One judge's typed per-dimension verdict (the judge cell's output schema).

    Dimensions are scored **independently** (pmm load-bearing): a post can be in the
    exact artist voice yet inappropriate, so a high ``voice`` must not pull ``appr``
    up. Each ``*_hard_fail`` is a tagged rubric disqualifier the aggregator reads as a
    floor (never averaged into the score).
    """

    model_config = {"frozen": True}

    voice: float = Field(ge=0.0, le=1.0, description="Brand-voice fidelity, 0-4 anchors normalized to [0,1].")
    safety: float = Field(ge=0.0, le=1.0, description="Safety, [0,1].")
    appr: float = Field(ge=0.0, le=1.0, description="Appropriateness, [0,1].")
    on_voice: bool = Field(description="Rubric brand-voice boolean (distinct from the graded voice score).")
    voice_hard_fail: bool = False
    safety_hard_fail: bool = False
    appr_hard_fail: bool = False
    rationale: str = Field(default="", description="One-line reason (audit; never gates).")


@dataclass(frozen=True)
class JudgeSpec:
    """One panel seat: its display name, model family, pinned model, and the prompt
    framing that makes it an INDEPENDENT reading (varied framings de-correlate the
    same-family jurors; the Ollama seat de-correlates by family)."""

    name: str
    family: str
    model: str
    framing: str


# The Phase-3/5 default panel: two Claude Opus jurors with DISTINCT framings + one
# local Ollama juror (the cross-family seat, no external key). Pins use the
# config model ids; the Ollama model id resolves once the provider extra is wired
# (the panel SHAPE — cross-family — holds now; the live call is gated).
DEFAULT_PANEL: tuple[JudgeSpec, ...] = (
    JudgeSpec("opus-strict", "anthropic", "anthropic:claude-opus-4-8",
              "Score STRICTLY against the rubric; when in doubt, score lower and tag a hard-fail."),
    JudgeSpec("opus-charitable", "anthropic", "anthropic:claude-opus-4-8",
              "Score the rubric charitably but flag any genuine disqualifier as a hard-fail."),
    JudgeSpec("ollama-cross", "ollama", "ollama:llama3.1",
              "Independent out-of-family read; score each dimension on its own merits."),
)

_JUDGE_INSTRUCTIONS = (
    "You are an independent brand-safety juror. Score ONE social post on three "
    "INDEPENDENT dimensions — voice (brand-voice fidelity), safety, appropriateness "
    "— each 0-4 on the rubric anchors, normalized to [0,1]. A post can be perfectly "
    "on-voice yet inappropriate; never let a high score on one dimension raise "
    "another. Set on_voice per the brand-voice rubric. Set <dim>_hard_fail=true ONLY "
    "for a tagged rubric DISQUALIFIER on that dimension (a hard appropriateness or "
    "safety violation), independent of the numeric score. {framing}"
)


def panel_families(panel: tuple[JudgeSpec, ...]) -> frozenset[str]:
    return frozenset(s.family for s in panel)


def is_cross_family(panel: tuple[JudgeSpec, ...]) -> bool:
    """At least two distinct model families on the panel (no single-family collapse)."""
    return len(panel_families(panel)) >= 2


def expected_judge_count(panel: tuple[JudgeSpec, ...] = DEFAULT_PANEL) -> int:
    return len(panel)


def build_judge_cell(spec: JudgeSpec, **overrides) -> Cell[JudgeScore]:
    """A typed judge cell for one panel seat (temp-0, pinned to the seat's model)."""
    params = dict(
        name=f"judge-{spec.name}",
        schema=JudgeScore,
        instructions=_JUDGE_INSTRUCTIONS.format(framing=spec.framing),
        model=spec.model,
    )
    params.update(overrides)
    return Cell(**params)


# A judge runner: scores one seat against the action, async. Injectable so the panel
# runs deterministically under FunctionModel in tests and real cells in production.
JudgeRunner = Callable[[JudgeSpec, str], Awaitable[JudgeScore]]


async def _default_runner(spec: JudgeSpec, action: str) -> JudgeScore:
    return await build_judge_cell(spec).run(action)


def _to_vote(spec: JudgeSpec, score: JudgeScore) -> JudgeVote:
    return JudgeVote(
        judge=spec.name,
        family=spec.family,
        voice=score.voice,
        safety=score.safety,
        appr=score.appr,
        on_voice=score.on_voice,
        voice_hard_fail=score.voice_hard_fail,
        safety_hard_fail=score.safety_hard_fail,
        appr_hard_fail=score.appr_hard_fail,
        reliability_weight=DEFAULT_WEIGHT,
    )


@dataclass(frozen=True)
class JuryRun:
    """The outcome of running the panel: who voted, who was dropped, expected size."""

    votes: list[JudgeVote]
    expected_judges: int
    dropped: list[tuple[str, str]] = field(default_factory=list)  # (judge_name, reason)


async def run_jury(
    action: str,
    *,
    panel: tuple[JudgeSpec, ...] = DEFAULT_PANEL,
    judge_runner: JudgeRunner | None = None,
    timeout_s: float = 30.0,
) -> JuryRun:
    """Run every panel seat on ``action`` concurrently and collect the votes.

    A seat that times out, errors, or refuses is **dropped** (absent from ``votes``,
    recorded in ``dropped``) — never counted as agreement, never blocks the run. The
    expected count is the full panel size, so the decision layer's degraded check
    sees the reduced coverage. If every seat fails, ``votes`` is empty and the
    decision layer fails safe to review (no confidence).
    """
    runner = judge_runner or _default_runner

    async def _one(spec: JudgeSpec) -> tuple[JudgeSpec, JudgeScore | None, str | None]:
        try:
            score = await asyncio.wait_for(runner(spec, action), timeout=timeout_s)
            return spec, score, None
        except asyncio.TimeoutError:
            return spec, None, "timeout"
        except Exception as exc:  # noqa: BLE001 — one bad judge never blocks the panel
            return spec, None, f"{type(exc).__name__}: {exc}"

    results = await asyncio.gather(*(_one(s) for s in panel))

    votes: list[JudgeVote] = []
    dropped: list[tuple[str, str]] = []
    for spec, score, reason in results:
        if score is None:
            dropped.append((spec.name, reason or "error"))
        else:
            votes.append(_to_vote(spec, score))
    return JuryRun(votes=votes, expected_judges=len(panel), dropped=dropped)
