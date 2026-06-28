"""Tests for the copywriter cell (CustomerAcq-1mk.5, Tier-3).

Two surfaces, both offline (no API key):

* **Pure validators** — the deterministic bank (variety, distinctness, platform
  length, and the S3 AI-flagger over nested variants) run directly over built
  models.
* **The cell** — driven by Pydantic-AI FunctionModel injection: returns typed
  drafts, repairs an AI-slop draft, and repairs an over-templated (duplicate-hook)
  draft.

Also checks that the S2 brand-voice context composes into the instructions.
"""

from __future__ import annotations

from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from cells.content_brief import Platform
from cells.copywriter import (
    CopyVariant,
    CopywriterDrafts,
    build_copywriter_cell,
    build_copywriter_instructions,
    copywriter_validators,
)
from cells.validators import Severity


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _variant(hook: str, caption: str, cta: str = "DM me to start your design.",
             pattern: str = "story-first", pillar: str = "made-for-you") -> dict:
    return {"pattern": pattern, "pillar": pillar, "hook": hook, "caption": caption,
            "call_to_action": cta}


def _drafts(*variants: dict, platform: str = "instagram", angle: str = "client-story") -> dict:
    return {"platform": platform, "angle": angle, "variants": list(variants)}


_GOOD = _drafts(
    _variant("She brought in her grandmother's recipe card.",
             "We spent the consult finding where it should sit. Healed and settled now."),
    _variant("First tattoo? We can go slow.",
             "Free 20-minute consult, no pressure to book. Bring the idea even if you can't draw it.",
             pattern="reassure-the-beginner", pillar="calm-room"),
)


def _model(*payloads: dict) -> FunctionModel:
    calls = {"n": 0}

    def fn(messages, info: AgentInfo) -> ModelResponse:
        idx = min(calls["n"], len(payloads) - 1)
        calls["n"] += 1
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, payloads[idx])])

    return FunctionModel(fn)


def _model_obj(d: dict) -> CopywriterDrafts:
    return CopywriterDrafts.model_validate(d)


# --------------------------------------------------------------------------- #
# pure validators
# --------------------------------------------------------------------------- #

def test_good_drafts_pass_the_bank():
    res = copywriter_validators().check(_model_obj(_GOOD))
    assert res.ok, res.summary()


def test_ai_tell_in_variant_is_flagged():
    bad = _drafts(
        _variant("Clean lines.", "We craft — carefully. Moreover, we take our time."),
        _variant("Second hook here.", "A perfectly fine second caption."),
    )
    res = copywriter_validators().check(_model_obj(bad))
    assert not res.ok
    assert any(i.validator == "ai_flagger" for i in res.errors)


def test_duplicate_hooks_are_flagged():
    dup = _drafts(
        _variant("Same hook.", "Caption one is here and fine."),
        _variant("Same hook.", "Caption two is different but the hook repeats."),
    )
    res = copywriter_validators().check(_model_obj(dup))
    assert not res.ok
    assert any(i.validator == "hooks_distinct" for i in res.errors)


def test_too_few_variants_is_an_error():
    one = _drafts(_variant("Only one hook.", "Only one caption, which is fine."))
    res = copywriter_validators().check(_model_obj(one))
    assert not res.ok
    assert any(i.validator == "variants_count" for i in res.errors)


def test_overlong_hook_is_flagged():
    longhook = " ".join(["word"] * 20)
    bad = _drafts(
        _variant(longhook, "A normal caption for this variant."),
        _variant("A short hook.", "Another normal caption."),
    )
    res = copywriter_validators().check(_model_obj(bad))
    assert not res.ok
    assert any(i.validator == "platform_length" for i in res.errors)


def test_facebook_uses_its_own_caption_cap():
    # Construct directly: a 3000-char caption is over IG's 2200 but under FB's 5000.
    big = "a" * 3000
    ig = CopywriterDrafts(platform=Platform.INSTAGRAM, angle="x",
                          variants=[CopyVariant(pattern="p", pillar="q", hook="h",
                                                caption=big, call_to_action="c"),
                                    CopyVariant(pattern="p", pillar="q", hook="h2",
                                                caption="ok", call_to_action="c")])
    fb = ig.model_copy(update={"platform": Platform.FACEBOOK})
    assert any(i.validator == "platform_length" for i in copywriter_validators().check(ig).errors)
    assert copywriter_validators().check(fb).ok


# --------------------------------------------------------------------------- #
# the cell (offline)
# --------------------------------------------------------------------------- #

def test_returns_typed_drafts():
    cell = build_copywriter_cell()
    out = cell.run_sync("angle: client-story; brand voice provided", model=_model(_GOOD))
    assert isinstance(out, CopywriterDrafts)
    assert len(out.variants) == 2


def test_slop_draft_is_repaired_then_accepted():
    slop = _drafts(
        _variant("Hook one.", "Moreover, we craft — carefully and well."),  # transition + em-dash
        _variant("Hook two.", "A clean second caption here."),
    )
    cell = build_copywriter_cell()
    out = cell.run_detailed_sync("angle", model=_model(slop, _GOOD))
    assert isinstance(out.value, CopywriterDrafts)
    assert out.repairs >= 1
    assert out.first_pass_valid is False


def test_over_templated_draft_is_repaired():
    templated = _drafts(
        _variant("Identical hook.", "Caption A is here and fine."),
        _variant("Identical hook.", "Caption B differs but the hook is the same."),
    )
    cell = build_copywriter_cell()
    out = cell.run_detailed_sync("angle", model=_model(templated, _GOOD))
    assert out.repairs >= 1


# --------------------------------------------------------------------------- #
# S2 brand-voice composition
# --------------------------------------------------------------------------- #

def test_instructions_compose_brand_voice_and_claims():
    ctx = "Positioning: quiet personal story. Do-not: 'unleash'."
    instr = build_copywriter_instructions(ctx, ("Free 20-minute consultation",))
    assert "quiet personal story" in instr
    assert "Free 20-minute consultation" in instr
    assert "BRAND VOICE WINS" in instr  # pattern-vs-voice edge case is in the rules
