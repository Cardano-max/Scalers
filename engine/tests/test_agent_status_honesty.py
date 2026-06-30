"""Honest per-agent status — pure/offline (no model, no DB).

The P1 bug: on the Agency page a COMPLETED campaign left required agents
(Strategist / Critic) stuck "queued" forever, even though the run was done. These
tests pin the honesty contract on the REAL run-state derivation
(``studio.campaign_runner.derive_agent_statuses``): a completed campaign NEVER leaves
any agent in a silent "queued" state — every agent is either ``done`` or carries an
honest reason it did not run (skipped-not-required / waiting-for-prev / failed /
blocked-missing-input / cancelled), derived from the actual ``agent_runs`` rows.
"""

from __future__ import annotations

from studio.campaign_runner import (
    AGENT_STATUS_DONE,
    AGENT_STATUS_FAILED,
    AGENT_STATUS_RUNNING,
    AGENT_STATUS_SKIPPED,
    AGENT_STATUS_WAITING,
    derive_agent_statuses,
    required_agent_roles,
)

# The dishonest sentinel the bug surfaced — it must never appear for any agent.
_QUEUED = "queued"


def _runs(*roles: str) -> list[dict]:
    """Synthetic agent_runs rows (only ``role`` matters to the derivation)."""
    return [{"role": r, "model": "m", "input": None, "output": {}} for r in roles]


def test_completed_provided_leads_run_has_no_agent_left_queued() -> None:
    # The exact "17 complete / 8 held" symptom: the provided-leads path records only
    # researcher + draft (x N) + a final jury, and NO strategist / critic. The run is
    # marked completed. Required agents must not be "queued"; the un-run ones read
    # their honest reason.
    agent_runs = _runs("researcher", "draft", "draft", "draft", "jury")
    statuses = derive_agent_statuses("provided_leads", agent_runs, "completed")

    # THE headline assertion: a completed campaign leaves NO agent silently "queued".
    assert _QUEUED not in statuses.values()

    assert statuses["researcher"] == AGENT_STATUS_DONE
    assert statuses["draft"] == AGENT_STATUS_DONE
    assert statuses["jury"] == AGENT_STATUS_DONE
    # The two agents this lead-targeted mode does not use are HONESTLY skipped.
    assert statuses["strategist"] == AGENT_STATUS_SKIPPED
    assert statuses["critic"] == AGENT_STATUS_SKIPPED


def test_completed_spine_run_marks_every_required_agent_done() -> None:
    # The full Phase-A spine (artist_spotlight enables B6/B7/B8/B9) runs every required
    # agent in sequence; a completed run has all of them done and none queued.
    required = required_agent_roles("artist_spotlight")
    assert {"strategist", "draft", "critic", "jury"} <= required

    agent_runs = _runs("strategist", "draft", "draft", "critic", "critic", "jury")
    statuses = derive_agent_statuses("artist_spotlight", agent_runs, "completed")

    assert _QUEUED not in statuses.values()
    for role in required:
        assert statuses[role] == AGENT_STATUS_DONE, role
    # research is not enabled for artist_spotlight -> honestly skipped, never queued.
    assert "researcher" not in required
    assert statuses["researcher"] == AGENT_STATUS_SKIPPED


def test_completed_run_never_yields_queued_for_any_partial_landing() -> None:
    # Property: for a COMPLETED run, no combination of landed roles produces "queued".
    seq = ["researcher", "strategist", "draft", "critic", "jury"]
    for cut in range(len(seq) + 1):
        statuses = derive_agent_statuses("provided_leads", _runs(*seq[:cut]), "completed")
        assert _QUEUED not in statuses.values(), seq[:cut]


def test_running_run_reports_in_flight_and_waiting_not_queued() -> None:
    # Mid-run: strategist landed, the next un-run agent is "running", later ones are
    # honestly "waiting-for-prev" (not a silent queue).
    statuses = derive_agent_statuses("artist_spotlight", _runs("strategist"), "running")
    assert _QUEUED not in statuses.values()
    assert statuses["strategist"] == AGENT_STATUS_DONE
    assert statuses["draft"] == AGENT_STATUS_RUNNING
    assert statuses["critic"] == AGENT_STATUS_WAITING
    assert statuses["jury"] == AGENT_STATUS_WAITING


def test_errored_run_marks_the_failing_agent_failed_not_queued() -> None:
    # An errored run that died after the strategist: the in-flight agent is "failed"
    # and everything downstream is "blocked-missing-input" — never "queued".
    statuses = derive_agent_statuses("artist_spotlight", _runs("strategist"), "error")
    assert _QUEUED not in statuses.values()
    assert statuses["draft"] == AGENT_STATUS_FAILED
