"""Tests for the copywriter EMAIL mode (CustomerAcq-1mk.5 / 1mk.7 handoff).

Offline (Pydantic-AI FunctionModel). Covers the email validator bank (lengths,
no social-isms, required unsubscribe token, no stray placeholders, S3 AI-flagger),
the cell repair loop, and S2 brand-voice composition.
"""

from __future__ import annotations

from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from cells.copywriter import (
    EmailCopy,
    TouchPurpose,
    UNSUBSCRIBE_TOKEN,
    build_copywriter_email_cell,
    build_copywriter_email_instructions,
    copywriter_email_validators,
)

_BODY = (
    "Hi Maria, I came across your neo-traditional work and the linework really "
    "stood out. I run a small custom studio and book by consult only. If you're "
    "planning your next piece, I'd love to chat. No worries either way.\n\n"
    f"{UNSUBSCRIBE_TOKEN}"
)
_GOOD = {"purpose": "intro", "subject": "Loved your floral linework", "body": _BODY}


def _email(**over) -> EmailCopy:
    d = dict(_GOOD)
    d.update(over)
    return EmailCopy.model_validate(d)


def _model(*payloads: dict) -> FunctionModel:
    calls = {"n": 0}

    def fn(messages, info: AgentInfo) -> ModelResponse:
        idx = min(calls["n"], len(payloads) - 1)
        calls["n"] += 1
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, payloads[idx])])

    return FunctionModel(fn)


# --- validators ------------------------------------------------------------ #

def test_good_email_passes():
    res = copywriter_email_validators().check(_email())
    assert res.ok, res.summary()


def test_missing_unsubscribe_is_error():
    res = copywriter_email_validators().check(_email(body="Hi Maria, quick note. Cheers."))
    assert any(i.validator == "email_requires_unsubscribe" for i in res.errors)


def test_unsubscribe_token_does_not_trip_placeholder():
    # the only "{{...}}" is the allowed token -> no no_placeholder error
    res = copywriter_email_validators().check(_email())
    assert not any(i.validator == "no_placeholder" for i in res.errors)


def test_stray_placeholder_is_error():
    res = copywriter_email_validators().check(
        _email(body=f"Hi {{{{name}}}}, quick note. {UNSUBSCRIBE_TOKEN}"))
    assert any(i.validator == "no_placeholder" for i in res.errors)


def test_hashtag_is_error():
    res = copywriter_email_validators().check(
        _email(body=f"Hi, check my work #tattoo. {UNSUBSCRIBE_TOKEN}"))
    assert any(i.validator == "email_no_socialisms" for i in res.errors)


def test_emoji_is_error():
    res = copywriter_email_validators().check(
        _email(body=f"Hi, loved your work 🌸. {UNSUBSCRIBE_TOKEN}"))
    assert any(i.validator == "email_no_socialisms" for i in res.errors)


def test_social_cta_is_error():
    res = copywriter_email_validators().check(
        _email(body=f"Hi, DM me to chat. {UNSUBSCRIBE_TOKEN}"))
    assert any(i.validator == "email_no_socialisms" for i in res.errors)


def test_overlong_subject_is_error():
    res = copywriter_email_validators().check(_email(subject="x" * 80))
    assert any(i.validator == "email_lengths" for i in res.errors)


def test_overlong_body_is_error():
    long_body = " ".join(["word"] * 200) + f" {UNSUBSCRIBE_TOKEN}"
    res = copywriter_email_validators().check(_email(body=long_body))
    assert any(i.validator == "email_lengths" for i in res.errors)


def test_ai_tell_in_email_is_flagged():
    res = copywriter_email_validators().check(
        _email(body=f"Hi, our work — moreover, it stands out. {UNSUBSCRIBE_TOKEN}"))
    assert any(i.validator == "ai_flagger" for i in res.errors)


# --- the cell (offline) ---------------------------------------------------- #

def test_cell_returns_typed_email():
    cell = build_copywriter_email_cell()
    out = cell.run_sync("touch: intro; brief: floral linework", model=_model(_GOOD))
    assert isinstance(out, EmailCopy)
    assert out.purpose is TouchPurpose.INTRO
    assert UNSUBSCRIBE_TOKEN in out.body


def test_cell_repairs_email_that_forgets_unsubscribe():
    cell = build_copywriter_email_cell()
    out = cell.run_detailed_sync(
        "touch",
        model=_model(
            {"purpose": "intro", "subject": "Hi", "body": "No unsubscribe here."},  # missing token
            _GOOD,                                                                   # fixed
        ),
    )
    assert UNSUBSCRIBE_TOKEN in out.value.body
    assert out.repairs >= 1


def test_cell_repairs_slop_email():
    cell = build_copywriter_email_cell()
    out = cell.run_detailed_sync(
        "touch",
        model=_model(
            {"purpose": "value-add", "subject": "Moreover, a tip",
             "body": f"Here's a tip — it's worth noting. {UNSUBSCRIBE_TOKEN}"},  # slop + hedge
            _GOOD,
        ),
    )
    assert out.repairs >= 1


# --- S2 brand-voice composition ------------------------------------------- #

def test_instructions_compose_brand_voice_and_email_rules():
    instr = build_copywriter_email_instructions(
        "Positioning: quiet personal story.", ("Free consult",))
    assert "quiet personal story" in instr
    assert "Free consult" in instr
    assert "{{unsubscribe}}" in instr
    assert "no hashtags" in instr.lower()
