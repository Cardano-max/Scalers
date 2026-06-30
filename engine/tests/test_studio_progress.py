"""Host progress-summary context — pure/offline tests (no DB, no network).

Proves the HEADLINE behaviour of ``build_progress_context``: the host's per-turn
view of the ACTIVE campaign run is computed from REAL state — the run's node spans
(agents by status), the per-role ``agent_runs`` (drafts created + research sources),
and the run's ``actions`` (review queue + sends) — and that with NO active run it is
honest-empty (every count zero, nothing invented). The three store reads are
monkeypatched here so the count logic is proven without Postgres, exactly the way
``test_studio_documents.py`` monkeypatches the document store.
"""

from __future__ import annotations

from types import SimpleNamespace

from studio import agui
from studio.agui import CampaignPlan, build_progress_context


def _span(status: str) -> SimpleNamespace:
    """A minimal stand-in for a runs-row node span (build_progress_context only
    reads ``.kind`` and ``.status``)."""
    return SimpleNamespace(kind="node", status=status)


def _action(run_id: str, status: str) -> SimpleNamespace:
    """A minimal stand-in for an ActionRow (only ``.run_id`` + ``.status`` are read)."""
    return SimpleNamespace(run_id=run_id, status=status)


_RUN_ID = "team-camp_abc123-def456"


def test_progress_reflects_real_run_state(monkeypatch) -> None:
    # A run whose per-agent node spans say: 3 completed, 1 failed, 1 running.
    record = SimpleNamespace(
        run_id=_RUN_ID,
        status=SimpleNamespace(value="completed"),
        steps=[_span("ok"), _span("ok"), _span("ok"), _span("failed"), _span("running")],
    )
    # Real per-role agent_runs: 2 researchers (3 + 2 cited = 5 sources) and 3 drafts.
    agent_runs = [
        {"role": "researcher", "output": {"cited": 3, "sources": [{}, {}, {}]}},
        {"role": "researcher", "output": {"cited": 2, "sources": [{}, {}]}},
        {"role": "strategist", "output": {"angle": "warm win-back"}},
        {"role": "draft", "output": {"hook": "a"}},
        {"role": "draft", "output": {"hook": "b"}},
        {"role": "draft", "output": {"hook": "c"}},
        {"role": "critic", "output": {"verdict": "ship"}},
    ]
    # This run's actions: 2 still pending (review queue), 1 sent, 1 failed.
    actions = [
        _action(_RUN_ID, "pending"),
        _action(_RUN_ID, "pending"),
        _action(_RUN_ID, "sent"),
        _action(_RUN_ID, "failed"),
        _action("team-other-run", "pending"),  # a DIFFERENT run — must be excluded
    ]
    monkeypatch.setattr(agui, "_tenant_runs", lambda tid, dsn=None: [record])
    monkeypatch.setattr(agui, "_tenant_actions", lambda tid, dsn=None: actions)
    monkeypatch.setattr(agui, "_agent_runs_for", lambda rid, dsn=None: agent_runs)

    plan = CampaignPlan(goal="win back lapsed clients", output_count=3)
    out = build_progress_context("ladies8391", plan, None)

    assert "CAMPAIGN PROGRESS" in out
    assert _RUN_ID in out
    assert "status: completed" in out
    # agents by status come from the run's node spans (real execution status)
    assert "completed=3" in out
    assert "failed=1" in out
    assert "running=1" in out
    # drafts created (3 draft agent_runs) vs expected (plan.output_count=3)
    assert "drafts: 3 created / 3 expected" in out
    # research sources summed across researcher outputs (3 + 2)
    assert "research sources found: 5" in out
    # review queue counts only THIS run's pending actions (2, not the other run's)
    assert "review queue (drafts staged HELD, approve-first): 2" in out
    # sends reflect the real terminal action statuses for this run
    assert "sends: 1 completed, 1 failed" in out


def test_progress_in_flight_run_before_runs_row(monkeypatch) -> None:
    # In-flight: the runs row is not materialized yet (no runs), but the spine has
    # already staged actions + recorded agent_runs under the new run_id. The summary
    # must surface THAT run as running, not report "no active run".
    in_flight = "team-camp_new999-aaa"
    monkeypatch.setattr(agui, "_tenant_runs", lambda tid, dsn=None: [])
    monkeypatch.setattr(
        agui, "_tenant_actions", lambda tid, dsn=None: [_action(in_flight, "pending")]
    )
    monkeypatch.setattr(
        agui,
        "_agent_runs_for",
        lambda rid, dsn=None: [
            {"role": "researcher", "output": {"cited": 4, "sources": []}},
            {"role": "draft", "output": {"hook": "x"}},
        ],
    )
    out = build_progress_context("ladies8391", CampaignPlan(output_count=1), None)
    assert in_flight in out
    assert "status: running" in out  # in-flight: row not materialized yet
    # no node spans yet -> agents fall back to recorded agent_runs (2 completed)
    assert "completed=2" in out
    assert "drafts: 1 created / 1 expected" in out
    assert "research sources found: 4" in out
    assert "review queue (drafts staged HELD, approve-first): 1" in out


def test_progress_honest_empty_when_no_active_run(monkeypatch) -> None:
    # No runs, no actions -> the host is told honestly that NO run is in flight and
    # every count is zero (never a fabricated draft / send count).
    monkeypatch.setattr(agui, "_tenant_runs", lambda tid, dsn=None: [])
    monkeypatch.setattr(agui, "_tenant_actions", lambda tid, dsn=None: [])
    monkeypatch.setattr(agui, "_agent_runs_for", lambda rid, dsn=None: [])
    out = build_progress_context("ladies8391", CampaignPlan(goal="g"), None)
    assert "NO active campaign run" in out
    assert "NEVER invent progress" in out
    # nothing fabricated: no run id, no per-count lines
    assert "team-camp_" not in out
    assert "drafts:" not in out
    assert "review queue" not in out
