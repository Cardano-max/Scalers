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
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from autonomy.decision import JudgeVote
from autonomy.rubric import (
    EXPECTED_CATALOG_VERSION,
    HardFailCatalog,
    load_hard_fail_catalog,
    resolve_codes,
)
from cells.base import Cell

# Local Ollama endpoint (OpenAI-compatible). Override via env for a remote/in-docker
# Ollama. The client provides only an Anthropic key, so this LOCAL juror (no key) is
# what makes the panel cross-family.
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1")

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
    # The rubric hard-fail / soft-cap codes this judge detected, from the CLOSED
    # catalog (rubric.code_catalog). The aggregator maps them to per-dimension floors
    # and fails safe on an unknown code. catalog_version is the version the judge
    # scored against — a mismatch with the aggregator's pinned version fails safe.
    hard_fail_codes: list[str] = Field(default_factory=list)
    catalog_version: int = EXPECTED_CATALOG_VERSION
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


# The Phase-3/5 default panel: two Claude Haiku jurors with DISTINCT framings +
# one local Ollama juror (the cross-family seat, no external key). Anthropic
# seats are POLICY-PINNED to haiku-4.5 (CustomerAcq-8sk; seat names stay honest
# about the model actually called). The Ollama model id resolves once the
# provider extra is wired (the panel SHAPE — cross-family — holds now).
DEFAULT_PANEL: tuple[JudgeSpec, ...] = (
    JudgeSpec("haiku-strict", "anthropic", "anthropic:claude-haiku-4-5",
              "Score STRICTLY against the rubric; when in doubt, score lower and tag a hard-fail."),
    JudgeSpec("haiku-charitable", "anthropic", "anthropic:claude-haiku-4-5",
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


def _model_for(spec: JudgeSpec):
    """Resolve a panel seat's model. Anthropic seats use the pinned model id string
    (pydantic-ai resolves it natively). The Ollama (local, cross-family) seat is built
    against the OpenAI-compatible Ollama endpoint via a LAZY import, so the core engine
    needs no extra provider package until an Ollama juror is actually run live — tests
    inject a ``judge_runner`` and never hit this path."""
    if spec.family != "ollama":
        return spec.model
    # Lazy: only import the OpenAI provider when actually constructing a live Ollama
    # judge. Missing extra -> ImportError, surfaced to the caller (the smoke skips).
    from pydantic_ai.models.openai import OpenAIModel
    from pydantic_ai.providers.openai import OpenAIProvider

    model_name = spec.model.split(":", 1)[1] if ":" in spec.model else spec.model
    return OpenAIModel(
        model_name,
        provider=OpenAIProvider(base_url=OLLAMA_BASE_URL, api_key="ollama"),
    )


def build_judge_cell(spec: JudgeSpec, **overrides) -> Cell[JudgeScore]:
    """A typed judge cell for one panel seat (temp-0, pinned to the seat's model).

    The Ollama seat is wired to the local Ollama OpenAI-compatible endpoint; provide a
    ``model=`` override (e.g. a ``FunctionModel``) to run any seat deterministically."""
    params = dict(
        name=f"judge-{spec.name}",
        schema=JudgeScore,
        instructions=_JUDGE_INSTRUCTIONS.format(framing=spec.framing),
        model=_model_for(spec),
    )
    params.update(overrides)
    return Cell(**params)


# A judge runner: scores one seat against the action, async. Injectable so the panel
# runs deterministically under FunctionModel in tests and real cells in production.
JudgeRunner = Callable[[JudgeSpec, str], Awaitable[JudgeScore]]


async def _default_runner(spec: JudgeSpec, action: str) -> JudgeScore:
    return await build_judge_cell(spec).run(action)


def _to_vote(spec: JudgeSpec, score: JudgeScore, catalog: HardFailCatalog) -> tuple[JudgeVote, bool, str]:
    """Map a judge's typed score to a vote, folding the resolved rubric CODES into
    per-dimension hard-fail floors and soft-cap score caps. Returns ``(vote,
    fail_safe, reason)`` — ``fail_safe`` (unknown code / version drift) forces REVIEW.
    Per-dimension bools the judge set directly are OR'd with the code-derived ones."""
    res = resolve_codes(score.hard_fail_codes, catalog=catalog, judge_catalog_version=score.catalog_version)
    scores = {"voice": score.voice, "safety": score.safety, "appr": score.appr}
    for dim, cap in res.soft_cap.items():  # soft-cap caps the dimension's score
        scores[dim] = min(scores[dim], cap)
    vote = JudgeVote(
        judge=spec.name,
        family=spec.family,
        voice=scores["voice"],
        safety=scores["safety"],
        appr=scores["appr"],
        on_voice=score.on_voice,
        voice_hard_fail=score.voice_hard_fail or ("voice" in res.hard_fail_dims),
        safety_hard_fail=score.safety_hard_fail or ("safety" in res.hard_fail_dims),
        appr_hard_fail=score.appr_hard_fail or ("appr" in res.hard_fail_dims),
        reliability_weight=DEFAULT_WEIGHT,
    )
    return vote, res.fail_safe, res.reason


@dataclass(frozen=True)
class JuryRun:
    """The outcome of running the panel: who voted, who was dropped, expected size,
    and whether any judge tripped a catalog FAIL-SAFE (unknown code / version drift)."""

    votes: list[JudgeVote]
    expected_judges: int
    dropped: list[tuple[str, str]] = field(default_factory=list)  # (judge_name, reason)
    catalog_drift: bool = False
    drift_reason: str = ""


async def run_jury(
    action: str,
    *,
    panel: tuple[JudgeSpec, ...] = DEFAULT_PANEL,
    judge_runner: JudgeRunner | None = None,
    timeout_s: float = 30.0,
    catalog: HardFailCatalog | None = None,
) -> JuryRun:
    """Run every panel seat on ``action`` concurrently and collect the votes.

    A seat that times out, errors, or refuses is **dropped** (absent from ``votes``,
    recorded in ``dropped``) — never counted as agreement, never blocks the run. The
    expected count is the full panel size, so the decision layer's degraded check
    sees the reduced coverage. If every seat fails, ``votes`` is empty and the
    decision layer fails safe to review (no confidence).

    Each judge's emitted rubric CODES are resolved against the closed ``catalog``:
    hard-fail codes become per-dimension floors, soft-caps cap the score, and an
    unknown code or a catalog_version drift sets ``catalog_drift`` so the decision
    layer fails safe to REVIEW (#81). The catalog is loaded once if not injected.
    """
    runner = judge_runner or _default_runner
    cat = catalog or load_hard_fail_catalog()

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
    catalog_drift = False
    drift_reason = ""
    for spec, score, reason in results:
        if score is None:
            dropped.append((spec.name, reason or "error"))
            continue
        vote, fail_safe, fs_reason = _to_vote(spec, score, cat)
        votes.append(vote)
        if fail_safe and not catalog_drift:
            catalog_drift, drift_reason = True, f"{spec.name}: {fs_reason}"
    return JuryRun(
        votes=votes, expected_judges=len(panel), dropped=dropped,
        catalog_drift=catalog_drift, drift_reason=drift_reason,
    )
