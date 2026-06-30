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
    mirroring what _execute_provided_leads_sync records. The strategist records the REAL
    planned lead count (n_leads) the run operated on."""
    return [
        {"seq": 0, "role": "strategist", "input": {"goal": "win-back", "n_leads": 10},
         "output": {"target_angle": "warm 90-day check-in"}},
        {"seq": 1, "role": "researcher", "input": {"customer_id": "c1", "name": "Mia"},
         "output": {"cited": 2, "sources": [{}, {}]}},
        {"seq": 2, "role": "draft", "input": {"customer_id": "c1", "channel": "email"},
         "output": {"hook": "..."}},
        {"seq": 3, "role": "critic", "input": {"customer_id": "c1", "channel": "email"},
         "output": {"verdict": "ship", "confidence": 0.8}},
        {"seq": 4, "role": "jury", "input": {"n_leads": 10},
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


def test_per_lead_roles_carry_real_x_of_n_progress() -> None:
    # N is the real planned lead count the run recorded (n_leads=10); X is the real
    # count of that role's steps done so far. Strategist / jury get no progress tag.
    narration = run_narration(_steps())
    by_role = {n["role"]: n["line"] for n in narration}
    assert "1 of 10" in by_role["researcher"]
    assert "1 of 10" in by_role["draft"]
    assert "1 of 10" in by_role["critic"]
    assert "of 10" not in by_role["strategist"]
    assert "of 10" not in by_role["jury"]


def test_x_counts_up_per_role_and_drops_when_total_unknown() -> None:
    # Two leads' worth of researcher steps with a known N=2 -> "1 of 2", "2 of 2".
    steps = [
        {"seq": 0, "role": "strategist", "input": {"n_leads": 2}, "output": {"target_angle": "a"}},
        {"seq": 1, "role": "researcher", "input": {"name": "Mia"}, "output": {"cited": 1}},
        {"seq": 2, "role": "researcher", "input": {"name": "Sam"}, "output": {"cited": 1}},
    ]
    lines = [n["line"] for n in run_narration(steps)]
    assert "1 of 2" in lines[1]
    assert "2 of 2" in lines[2]
    # With NO recorded total (a content campaign, no n_leads), the X-of-N tag is dropped
    # rather than fabricated.
    no_total = [{"seq": 0, "role": "draft", "input": {"channel": "instagram"}, "output": {"hook": "x"}}]
    line = run_narration(no_total)[0]["line"]
    assert "of" not in line.split("instagram")[-1]  # no fabricated "X of N"


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
