"""P3 voice-layer tests — hermetic (no network, no DB).

Proves the SERVER-SIDE posture that makes the voice agent safe:
* the tool surface exposed to the realtime model is EXACTLY two tools
  (``update_plan`` + ``request_orchestration``) and contains NO send/publish tool;
* the GO-gate is a SERVER-SIDE 2-factor decision: launch iff (1) the plan is
  readback-ready (AWAITING_GO armed server-side) AND (2) the utterance is an
  explicit go-phrase that is not an edit;
* a mid-interview "go ahead" does NOT launch (factor 1 fails);
* "go ahead and add instagram" is classified an EDIT, never a launch (factor 2);
* the minted session config declares only the two tools.
"""

from __future__ import annotations

from studio.agui import CampaignPlan
from studio.voice import (
    VOICE_TOOL_NAMES,
    VOICE_TOOLS,
    build_session_config,
    classify_utterance,
    evaluate_go_gate,
    plan_is_runnable,
)


# --------------------------------------------------------------------------- #
# Tool surface — structurally incapable of sending
# --------------------------------------------------------------------------- #


def test_voice_tool_surface_is_exactly_two() -> None:
    assert VOICE_TOOL_NAMES == ("update_plan", "request_orchestration")
    assert len(VOICE_TOOLS) == 2


def test_voice_tool_surface_has_no_send_or_publish_tool() -> None:
    names = {t["name"] for t in VOICE_TOOLS}
    blob = " ".join(t["name"] for t in VOICE_TOOLS).lower()
    for forbidden in ("publish", "send", "stage", "post", "email", "approve"):
        assert forbidden not in names
        assert forbidden not in blob


def test_voice_instructions_inject_active_docs_but_keep_two_tools(monkeypatch) -> None:
    """The voice supervisor is told it HAS the active docs (so it can say 'yes, I have
    your brand playbook'), and the injection adds NO tool — the surface stays exactly
    two (send-incapable)."""
    from studio import documents as docstore
    from studio.voice import voice_instructions_with_docs

    monkeypatch.setattr(
        docstore, "active_docs_index",
        lambda tid, dsn=None: [
            {"name": "Ladies First Brand & Campaign Playbook",
             "summary": "Woman-owned Austin color studio."}
        ],
    )
    instr = voice_instructions_with_docs("ladies8391", dsn=None)
    assert "Ladies First Brand & Campaign Playbook" in instr
    assert "cannot send or publish" in instr
    cfg = build_session_config(instructions=instr)
    assert [t["name"] for t in cfg["tools"]] == ["update_plan", "request_orchestration"]


def test_voice_instructions_honest_when_no_docs(monkeypatch) -> None:
    from studio import documents as docstore
    from studio.voice import voice_instructions_with_docs

    monkeypatch.setattr(docstore, "active_docs_index", lambda tid, dsn=None: [])
    instr = voice_instructions_with_docs("ladies8391", dsn=None)
    assert "NO uploaded documents" in instr
    assert "Ladies First" not in instr  # nothing fabricated


def test_minted_session_declares_only_the_two_tools() -> None:
    cfg = build_session_config()
    assert [t["name"] for t in cfg["tools"]] == ["update_plan", "request_orchestration"]
    # input transcription enabled so the server receives the go-phrase utterance
    assert cfg["audio"]["input"]["transcription"]["model"]


# --------------------------------------------------------------------------- #
# Arming predicate (factor 1) — derived server-side from the plan
# --------------------------------------------------------------------------- #


def test_plan_is_runnable_requires_goal_audience_channels() -> None:
    assert not plan_is_runnable(CampaignPlan())
    assert not plan_is_runnable(CampaignPlan(goal="g", audience="a"))  # no channel
    assert not plan_is_runnable(CampaignPlan(goal="g", channels=["instagram"]))  # no audience
    assert plan_is_runnable(
        CampaignPlan(goal="book consults", audience="lapsed clients", channels=["instagram"])
    )


# --------------------------------------------------------------------------- #
# Utterance classification (factor 2)
# --------------------------------------------------------------------------- #


def test_explicit_go_phrases_classify_as_go() -> None:
    for phrase in ("go", "run it", "let's go", "lets go", "do it", "kick it off", "go ahead"):
        assert classify_utterance(phrase) == "go", phrase


def test_edit_utterances_classify_as_edit_not_go() -> None:
    # the canonical false-positive: a go-word inside an edit instruction
    assert classify_utterance("go ahead and add instagram") == "edit"
    assert classify_utterance("change the audience to new clients") == "edit"
    assert classify_utterance("also include email") == "edit"
    assert classify_utterance("what about adding a discount") == "edit"


def test_go_word_does_not_match_substrings() -> None:
    # "go" must not fire on instagram / ago
    assert classify_utterance("target instagram followers") == "other"


# --------------------------------------------------------------------------- #
# The 2-factor GO-gate decision
# --------------------------------------------------------------------------- #


def test_mid_interview_go_ahead_does_not_launch() -> None:
    """The headline guarantee: a 'go ahead' spoken MID-INTERVIEW (plan not yet
    readback-ready, so AWAITING_GO is NOT armed) must NOT launch orchestration."""
    decision = evaluate_go_gate(awaiting_go=False, transcript="go ahead")
    assert decision["launch"] is False
    assert decision["armed"] is False


def test_edit_utterance_does_not_launch_even_when_armed() -> None:
    decision = evaluate_go_gate(awaiting_go=True, transcript="go ahead and add instagram")
    assert decision["launch"] is False
    assert decision["classification"] == "edit"


def test_armed_plus_explicit_go_launches() -> None:
    for phrase in ("go", "run it", "let's go", "do it", "kick it off"):
        decision = evaluate_go_gate(awaiting_go=True, transcript=phrase)
        assert decision["launch"] is True, phrase


def test_armed_but_no_go_phrase_does_not_launch() -> None:
    decision = evaluate_go_gate(awaiting_go=True, transcript="hmm, looks good I think")
    assert decision["launch"] is False
    assert decision["classification"] == "other"
