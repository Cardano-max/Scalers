"""Live team-narration tests (#11) — pure/offline (no model, no DB).

Proves that ``run_narration`` is a HONEST projection of the run's REAL recorded
``agent_runs`` steps (the same per-role steps ``GET /studio/run/{id}`` returns):
* one narration line per recorded step, in order — never more, never a stage that
  did not actually run;
* each line names the REAL lead / channel from the step's own input;
* a failed strategist / critic step is narrated as a snag, never as success.
"""

from __future__ import annotations

from studio.agui import run_narration


def _steps() -> list[dict]:
    """A realistic recorded run: strategist -> per-lead [researcher, draft, critic] -> jury,
    mirroring what _execute_provided_leads_sync records."""
    return [
        {"seq": 0, "role": "strategist", "input": {"goal": "win-back"},
         "output": {"target_angle": "warm 90-day check-in"}},
        {"seq": 1, "role": "researcher", "input": {"customer_id": "c1", "name": "Mia"},
         "output": {"cited": 2, "sources": [{}, {}]}},
        {"seq": 2, "role": "draft", "input": {"customer_id": "c1", "channel": "email"},
         "output": {"hook": "..."}},
        {"seq": 3, "role": "critic", "input": {"customer_id": "c1", "channel": "email"},
         "output": {"verdict": "ship", "confidence": 0.8}},
        {"seq": 4, "role": "jury", "input": {"n_leads": 1},
         "output": {"note": "1 draft staged HELD; approve-first"}},
    ]


def test_one_line_per_real_step_in_order() -> None:
    steps = _steps()
    narration = run_narration(steps)
    # Exactly one narration entry per recorded step — no fabricated extra stages.
    assert len(narration) == len(steps)
    assert [n["role"] for n in narration] == ["strategist", "researcher", "draft", "critic", "jury"]
    assert [n["seq"] for n in narration] == [0, 1, 2, 3, 4]


def test_lines_name_the_real_lead_and_channel() -> None:
    narration = run_narration(_steps())
    by_role = {n["role"]: n["line"] for n in narration}
    assert "warm 90-day check-in" in by_role["strategist"]
    assert "Mia" in by_role["researcher"]
    assert "email" in by_role["draft"]
    assert "ship" in by_role["critic"]
    assert "held" in by_role["jury"].lower()
    assert all(n["failed"] is False for n in narration)


def test_failed_steps_are_narrated_honestly() -> None:
    steps = [
        {"seq": 0, "role": "strategist", "input": {}, "output": {"status": "failed", "error": "boom"}},
        {"seq": 1, "role": "critic", "input": {"channel": "email"},
         "output": {"verdict": "error", "rationale": "cell failed"}},
    ]
    narration = run_narration(steps)
    assert narration[0]["failed"] is True
    assert "snag" in narration[0]["line"].lower()
    assert narration[1]["failed"] is True
    assert "flagged" in narration[1]["line"].lower()


def test_empty_run_narrates_nothing() -> None:
    assert run_narration([]) == []
    assert run_narration(None) == []
