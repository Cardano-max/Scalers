"""CustomerAcq-tlv.3 regression tests — the client transcript must read like an
agency, never like plumbing.

Live drive (2026-07-03, session studio-live-session) proved the persisted studio
chat leaked: raw planner JSON, 24x duplicate per-lead analyst rows, raw
CellExecutionError text, double-persisted operator turns (approval-resume
re-POSTs the same last user message), and silent 0-draft host summaries. These
tests pin the fixed behavior: ``chat_mirror_turns`` collapses a run's agent_runs
into ONE human turn per role, ``_operator_turn_text`` refuses the resume
double-persist, and ``_summary_text`` explains a 0-draft run honestly. All pure
— no DB, no network.
"""

from __future__ import annotations

from studio.agui import _operator_turn_text, _summary_text, chat_mirror_turns


def _analyst_run(lead: str, objection: str = "none-found") -> dict:
    return {
        "role": "analyst",
        "model": "gemini-flash",
        "input": {"name": lead},
        "output": {
            "umbrella_category": "open-warm-lead",
            "primary_objection": objection,
            "where_customer_sits": "open warm lead",
        },
        "output_summary": f"open-warm-lead · objection={objection} · open warm lead",
    }


def _planner_run() -> dict:
    return {
        "role": "planner",
        "model": "claude-opus",
        "input": {"goal": "win back lapsed clients"},
        "output": {
            "targets": {"category": "all", "description": "all eligible leads"},
            "per_channel_quota": {"sms": 12},
            "stop_conditions": {"total_quota": 12},
            "angle": "welcome back — your artist misses you",
            "blueprint": {"goal": "win back lapsed clients"},
        },
        "output_summary": '{"targets": {"category": "all"}}',
    }


def _critic_run(lead: str, *, error: bool = False) -> dict:
    if error:
        out = {
            "verdict": "error",
            "confidence": 0.0,
            "rationale": (
                "critic cell failed: CellExecutionError: cell 'critic' failed to "
                "execute: ModelHTTPError: status_code: 400"
            ),
        }
    else:
        out = {"verdict": "pass", "confidence": 0.9, "rationale": "on-brand"}
    return {
        "role": "critic",
        "model": "claude-haiku",
        "input": {"name": lead, "channel": "sms"},
        "output": out,
        "output_summary": f"verdict={out['verdict']} ({out['confidence']})",
    }


class TestChatMirrorCollapse:
    def test_analyst_spam_collapses_to_one_human_turn(self) -> None:
        """24 per-lead analyst agent_runs (the live 12-leads-x-2-passes flood) must
        become ONE readable turn carrying the real count — not 24 rows."""
        runs = [_analyst_run(f"lead-{i}") for i in range(24)]
        turns = chat_mirror_turns(runs)
        analyst = [t for t in turns if t[0] == "analyst"]
        assert len(analyst) == 1
        text = analyst[0][1]
        assert "24" in text
        assert "[analyst]" not in text
        assert "·" not in text  # the raw output_summary separator must not leak

    def test_planner_turn_is_human_not_json(self) -> None:
        turns = chat_mirror_turns([_planner_run()])
        planner = [t for t in turns if t[0] == "planner"]
        assert len(planner) == 1
        text = planner[0][1]
        assert "{" not in text and "}" not in text
        assert "[planner]" not in text
        # carries something real from the plan, not a generic platitude
        assert "12" in text or "all eligible leads" in text

    def test_critic_error_is_honest_one_liner(self) -> None:
        """A failed critic cell reads as an honest flag, not a stack trace."""
        runs = [_critic_run("a"), _critic_run("b", error=True), _critic_run("c")]
        turns = chat_mirror_turns(runs)
        critic = [t for t in turns if t[0] == "critic"]
        assert len(critic) == 1
        text = critic[0][1]
        assert "CellExecutionError" not in text
        assert "ModelHTTPError" not in text
        # honest: the failure is stated (1 of 3 failed), not hidden
        assert "1" in text and "3" in text

    def test_unknown_role_is_narrated_not_dropped(self) -> None:
        runs = [{"role": "qa_bot", "model": None, "input": {}, "output": {"ok": True},
                 "output_summary": "ok"}]
        turns = chat_mirror_turns(runs)
        assert len(turns) == 1

    def test_roles_keep_first_appearance_order(self) -> None:
        runs = [_planner_run(), _analyst_run("a"), _critic_run("a"), _analyst_run("b")]
        roles = [t[0] for t in chat_mirror_turns(runs)]
        assert roles == ["planner", "analyst", "critic"]


class TestOperatorTurnResumeDoublePersist:
    def test_plain_send_persists_last_user_message(self) -> None:
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
            {"role": "user", "content": "run a win-back campaign"},
        ]
        assert _operator_turn_text(msgs) == "run a win-back campaign"

    def test_approval_resume_does_not_repersist_operator_turn(self) -> None:
        """The approve/reject resume re-POSTs the SAME thread with an assistant
        tool-call message appended — the operator said nothing new, so nothing new
        may be persisted (this was the live double-operator-turn bug)."""
        msgs = [
            {"role": "user", "content": "run a win-back campaign"},
            {"role": "assistant", "content": "", "toolCalls": [{"id": "t1"}]},
        ]
        assert _operator_turn_text(msgs) == ""

    def test_empty_messages_is_empty(self) -> None:
        assert _operator_turn_text([]) == ""


class TestZeroDraftSummaryExplainsItself:
    def test_zero_draft_run_carries_skip_reason(self) -> None:
        summary = {
            "archetype_id": "provided_leads",
            "run_id": "team-x",
            "n_queued": 0,
            "n_pending": 0,
            "channels": ["sms"],
            "runs_row": True,
            "output_ledger": {
                "expected": 3,
                "drafted": 0,
                "skipped": [
                    {"row": 1, "reason": "no phone number"},
                    {"row": 2, "reason": "no phone number"},
                    {"row": 3, "reason": "opted out"},
                ],
                "reconciled": True,
            },
        }
        text = _summary_text(summary)
        assert "no phone number" in text
        assert "opted out" in text

    def test_zero_draft_run_carries_failure_reason(self) -> None:
        summary = {
            "archetype_id": "compose",
            "run_id": "team-y",
            "n_queued": 0,
            "n_pending": 0,
            "channels": [],
            "runs_row": True,
            "failure_summary": [
                {"agent": "strategist", "error": "credit exhausted", "step": "strategy"},
            ],
        }
        text = _summary_text(summary)
        assert "strategist" in text
        assert "credit exhausted" in text

    def test_zero_draft_run_without_recorded_reason_is_honest(self) -> None:
        summary = {
            "archetype_id": "compose", "run_id": "team-z",
            "n_queued": 0, "n_pending": 0, "channels": [], "runs_row": True,
        }
        text = _summary_text(summary)
        # says zero happened and does NOT invent a reason
        assert "no" in text.lower() or "0" in text
        assert "Runs" in text

    def test_normal_run_summary_unchanged_shape(self) -> None:
        summary = {
            "archetype_id": "provided_leads", "run_id": "team-ok",
            "n_queued": 5, "n_pending": 5, "channels": ["sms"], "runs_row": True,
        }
        text = _summary_text(summary)
        assert "5 draft(s)" in text
        assert "nothing was sent" in text
