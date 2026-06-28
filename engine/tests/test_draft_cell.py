"""Tests for the Draft (Create) cell (CustomerAcq-a9m.5 / POST-01b).

Offline (Pydantic-AI FunctionModel). Covers: typed PostDraft out; banned/AI-tell ->
repair-then-accept and persistent -> CellError (never raw); over-length flagged;
per-kind media coherence; claim discipline; SPARSE grounding -> dimensions-only;
hashtags policy; and PostDraft persistence in GraphState. Grounding is the real
kb.voice.VoiceGrounding contract (a9m.3).
"""

from __future__ import annotations

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from cells.base import CellError
from cells.draft import build_draft_cell, draft_validators, persist_draft, render_angle_prompt
from cells.post_schemas import MediaKind, Platform, PostDraft
from harness.state import GraphState
from kb.voice import (
    Exemplar,
    GroundingCoverage,
    VoiceDimensions,
    VoiceGrounding,
    Vocabulary,
)

# ── grounding fixtures (real a9m.3 contract) ─────────────────────────────────

_VOCAB = Vocabulary(
    prefer=["made for you", "your story", "cover-up", "reclaim"],
    ban=["unleash", "slay", "queen", "best", "#1", "painless", "transform your look", "glow-up"],
    approved_claims=[
        "Woman-owned, appointment-only studio in Austin, TX.",
        "9 years tattooing, specializing in neo-traditional color and floral work.",
        "Free consultation before every booking.",
        "Custom designs drawn for you, no flash copies.",
    ],
    emoji_policy="0-2 per caption, only 🌸 🌷 🤍",
    hashtag_policy="3-6, lowercase, specific",
)
_DIMS = VoiceDimensions(
    tone=["warm, direct; first-person 'I' (Rae); soft-invite CTA"],
    structure=["short, one idea per line", "open on the client's story"],
    vocabulary=_VOCAB,
)
_EXEMPLARS = [
    Exemplar(
        content="She brought in her grandmother's garden and we drew flowers that grew there. Healed now.",
        metrics={"on_voice": True},
        similarity=0.91,
    ),
    Exemplar(
        content="First tattoo? We go slow. Free consult, no pressure.", metrics={}, similarity=0.84
    ),
]


def grounding(coverage: GroundingCoverage = GroundingCoverage.FULL) -> VoiceGrounding:
    ex = [] if coverage is GroundingCoverage.SPARSE else _EXEMPLARS
    return VoiceGrounding(
        tenant_id="ladies8391",
        dimensions=_DIMS,
        exemplars=ex,
        coverage=coverage,
        low_grounding=coverage is GroundingCoverage.SPARSE,
        exemplar_count=len(ex),
    )


# ── PostDraft payloads (FunctionModel output-tool calls) ─────────────────────


def _media(kind="image", ar="4:5", dur=None, brief="floral forearm, natural light"):
    return {"kind": kind, "aspect_ratio": ar, "duration_s": dur, "brief": brief}


def _draft(
    caption, *, hashtags=None, cta="DM me to start your design.", media=None, platform="instagram"
):
    return {
        "platform": platform,
        "caption": caption,
        "hashtags": ["neotraditionaltattoo", "floraltattoo", "austintattoo"]
        if hashtags is None
        else hashtags,
        "call_to_action": cta,
        "media": media or _media(),
    }


_GOOD = _draft(
    "She brought in her mom's garden and we drew three flowers that actually grew "
    "there. Healed and settled now. 🌸"
)
_BANNED = _draft("Unleash your inner queen 🌸 such a glow-up, book now.")
_AITELL = _draft("In today's world, it's not just a tattoo, it is a statement.")
_BADCLAIM = _draft("100% painless and guaranteed. 12 years and counting.")


def _model(*payloads):
    calls = {"n": 0}

    def fn(messages, info: AgentInfo) -> ModelResponse:
        idx = min(calls["n"], len(payloads) - 1)
        calls["n"] += 1
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, payloads[idx])])

    return FunctionModel(fn)


def _cell(coverage=GroundingCoverage.FULL, **kw):
    return build_draft_cell(grounding=grounding(coverage), platform=Platform.INSTAGRAM, **kw)


def _validators(coverage=GroundingCoverage.FULL):
    return draft_validators(grounding=grounding(coverage), platform=Platform.INSTAGRAM)


_ANGLE = render_angle_prompt(
    hook="reclaim a scar with a floral piece",
    rationale="reclaim pillar + the client's story",
    format_hint=MediaKind.IMAGE,
)


# ── the cell (offline) ───────────────────────────────────────────────────────


def test_returns_typed_postdraft():
    out = _cell().run_sync(_ANGLE, model=_model(_GOOD))
    assert isinstance(out, PostDraft)
    assert out.media.kind is MediaKind.IMAGE and out.platform is Platform.INSTAGRAM


def test_banned_draft_repaired_then_accepted():
    out = _cell().run_detailed_sync("angle", model=_model(_BANNED, _GOOD))
    assert isinstance(out.value, PostDraft)
    assert out.repairs >= 1 and out.first_pass_valid is False


def test_persistent_banned_raises_cellerror_never_raw():
    cell = _cell(retries=1)
    with pytest.raises(CellError):
        cell.run_sync("angle", model=_model(_BANNED, _BANNED, _BANNED))


def test_ai_tell_draft_is_repaired():
    out = _cell().run_detailed_sync("angle", model=_model(_AITELL, _GOOD))
    assert out.repairs >= 1


# ── deterministic validator bank ─────────────────────────────────────────────


def test_good_draft_passes_bank():
    res = _validators().check(PostDraft.model_validate(_GOOD))
    assert res.ok, res.summary()


def test_overlong_caption_flagged():
    res = _validators().check(PostDraft.model_validate(_draft("a" * 2300)))
    assert any(i.validator == "caption_length" for i in res.errors)


def test_banned_phrase_flagged():
    res = _validators().check(PostDraft.model_validate(_BANNED))
    assert any(i.validator == "ban_lexicon" for i in res.errors)


def test_unapproved_claim_flagged():
    res = _validators().check(PostDraft.model_validate(_BADCLAIM))
    vs = {i.validator for i in res.errors}
    assert "claim_discipline" in vs  # '100 %', '12 years', 'guaranteed' not approved
    assert "ban_lexicon" in vs  # 'painless' is a hard ban


def test_ai_tell_flagged_in_bank():
    res = _validators().check(PostDraft.model_validate(_AITELL))
    assert any(i.validator == "ai_flagger" for i in res.errors)


@pytest.mark.parametrize(
    "media,ok",
    [
        (_media("reel", "9:16", 22.0), True),
        (_media("reel", "9:16", None), False),  # reel needs duration
        (_media("reel", "9:16", 150.0), False),  # >90s
        (_media("image", "4:5", None), True),
        (_media("carousel", "1:1", None), True),
        (_media("text", None, None), True),
        (_media("image", None, None), False),  # image needs aspect_ratio
    ],
)
def test_media_coherence(media, ok):
    res = _validators().check(
        PostDraft.model_validate(_draft("A clean on-voice caption about the work.", media=media))
    )
    has_media_err = any(i.validator == "media_valid" for i in res.errors)
    assert has_media_err == (not ok)


def test_hashtag_wall_and_case_flagged():
    res = _validators().check(
        PostDraft.model_validate(
            _draft("A clean caption.", hashtags=["#Tattoo", "SLAY"] + [f"t{i}" for i in range(12)])
        )
    )
    assert any(i.validator == "hashtags_policy" for i in res.errors)


# ── SPARSE grounding (degrade ladder) ────────────────────────────────────────


def test_sparse_grounding_dimensions_only():
    from cells.draft import build_draft_instructions

    instr = build_draft_instructions(grounding(GroundingCoverage.SPARSE), Platform.INSTAGRAM)
    assert "sparse grounding" in instr.lower()
    # still produces a valid draft from dimensions alone
    out = build_draft_cell(
        grounding=grounding(GroundingCoverage.SPARSE), platform=Platform.INSTAGRAM
    ).run_sync("angle", model=_model(_GOOD))
    assert isinstance(out, PostDraft)


# ── persistence in GraphState ────────────────────────────────────────────────


def test_postdraft_persists_in_graphstate():
    state = GraphState(tenant_id="ladies8391", run_id="r1", topic="floral cover-up")
    out = _cell().run_sync(_ANGLE, model=_model(_GOOD))
    state2 = persist_draft(state, out)
    assert state2.draft == out
    assert any("draft:" in s for s in state2.step_log)


# ── S2 brand-voice composition into instructions ─────────────────────────────


def test_instructions_compose_grounding():
    from cells.draft import build_draft_instructions

    instr = build_draft_instructions(grounding(), Platform.INSTAGRAM)
    assert "Free consultation before every booking." in instr  # approved claim
    assert "unleash" in instr.lower()  # ban surfaced
    assert "BRAND VOICE WINS" in instr
