"""nmh.9 acceptance — the dispatcher routes by CHANNEL, proven at the dispatch seam.

Drives the REAL ``studio.agui._execute_campaign_sync`` (the one dispatcher both the
voice GO-gate and POST /studio/run funnel through) with the two pipeline functions
SPIED, so we prove — deterministically, no LLM — exactly which workflow each request
runs and with what archetype (the value the spine writes into the trace):

  * 'create an Instagram post' → the IG compose spine (archetype_id='artist_spotlight'),
    NOT the email-outreach path;
  * 'send emails' + provided leads → the email outreach path, NOT the compose spine;
  * 'Facebook campaign' → the FB page-post compose spine (archetype_id='facebook_post'),
    NOT the email-outreach path;
  * 'artist with attachments' → an HONEST not-built response:
    NEITHER pipeline runs (zero fake run), run_status='not_built'.
"""

from __future__ import annotations

import studio.agui as agui
import studio.campaign_runner as runner_mod
from studio.agui import CampaignPlan, _execute_campaign_sync


class _Blueprint:
    """Minimal stand-in for the planner's blueprint (the dispatch only passes it on)."""

    planner_model = "test"

    def model_dump(self):
        return {}


def _spy_pipelines(monkeypatch):
    """Replace both pipelines with recorders + stub the planner/log/persist seams, so
    the test exercises ONLY the routing decision (no model calls, no DB)."""
    calls: dict[str, dict] = {}

    monkeypatch.setattr(agui, "plan_campaign", lambda plan, tenant, dsn: _Blueprint())
    monkeypatch.setattr(agui, "_log_turn", lambda *a, **k: None)
    monkeypatch.setattr(agui, "_record_planner_run", lambda *a, **k: None)
    monkeypatch.setattr(agui, "_persist_plan", lambda *a, **k: None)
    monkeypatch.setattr(agui, "_persist_campaign_spec", lambda *a, **k: None)

    def _fake_run_and_trace(**kw):
        calls["compose"] = kw
        aid = kw.get("archetype_id") or "win_back"
        # Return a realistic trace summary — the fields the run row / poller read.
        if aid == "artist_spotlight":
            chans = ["ig", "reels", "email"]
        elif aid == "facebook_post":
            chans = ["fb", "email"]
        else:
            chans = ["email"]
        return {
            "run_id": kw.get("run_id"),
            "campaign_id": "camp_x",
            "archetype_id": aid,
            "agent_runs": [{"role": "draft", "model": "m"}],
            "channels": chans,
            "n_pending": 1,
            "n_queued": 1,
            "runs_row": True,
            "run_status": "completed",
            "failure_summary": [],
        }

    def _fake_provided(plan, session_id, tenant_id, dsn, run_id, *, blueprint=None):
        calls["email"] = {"run_id": run_id}
        return {
            "run_id": run_id,
            "campaign_id": "camp_x",
            "archetype_id": "provided_leads",
            "lead_source": "provided",
            "agent_runs": [{"role": "draft"}],
            "channels": ["gmail"],
            "n_pending": 1,
            "n_queued": 1,
            "runs_row": True,
            "run_status": "completed",
            "failure_summary": [],
        }

    monkeypatch.setattr(runner_mod, "run_and_trace", _fake_run_and_trace)
    monkeypatch.setattr(agui, "_execute_provided_leads_sync", _fake_provided)
    return calls


def test_instagram_post_runs_ig_spine_not_email(monkeypatch):
    calls = _spy_pipelines(monkeypatch)
    plan = CampaignPlan(
        goal="create an Instagram post for our new artist",
        audience="local clients",
        channels=["instagram"],
    )
    summary = _execute_campaign_sync(plan, "sess", "t", None, run_id="team-camp_x-abc")

    assert "compose" in calls and "email" not in calls  # IG spine ran, NOT email
    assert calls["compose"]["archetype_id"] == "artist_spotlight"  # the IG archetype
    assert summary["routed_channel"] == "instagram" and summary["pipeline_built"] is True
    assert summary["archetype_id"] == "artist_spotlight"
    assert "ig" in summary["channels"]  # the trace proves IG ran


def test_send_emails_provided_runs_email_not_compose(monkeypatch):
    calls = _spy_pipelines(monkeypatch)
    plan = CampaignPlan(
        goal="send emails to win back lapsed clients",
        audience="lapsed clients",
        channels=["email"],
        lead_source="provided",
    )
    summary = _execute_campaign_sync(plan, "sess", "t", None, run_id="team-camp_x-abc")

    assert "email" in calls and "compose" not in calls  # email path ran, NOT compose
    assert summary["routed_channel"] == "email" and summary["pipeline_built"] is True


def test_facebook_campaign_runs_fb_spine_not_email(monkeypatch):
    calls = _spy_pipelines(monkeypatch)
    plan = CampaignPlan(
        goal="create a Facebook campaign for the flash-day promo",
        audience="all",
        channels=["facebook"],
    )
    summary = _execute_campaign_sync(plan, "sess", "t", None, run_id="team-camp_x-abc")

    assert "compose" in calls and "email" not in calls  # FB spine ran, NOT email
    assert calls["compose"]["archetype_id"] == "facebook_post"  # the FB archetype
    assert summary["routed_channel"] == "facebook" and summary["pipeline_built"] is True
    assert summary["archetype_id"] == "facebook_post"
    assert "fb" in summary["channels"]  # the trace proves FB ran


def test_messenger_dm_is_honest_not_built_no_run(monkeypatch):
    calls = _spy_pipelines(monkeypatch)
    plan = CampaignPlan(
        goal="send messenger DMs to everyone who commented",
        audience="all",
        channels=["facebook"],
    )
    summary = _execute_campaign_sync(plan, "sess", "t", None, run_id="team-camp_x-abc")

    # NEITHER pipeline ran — DMs stay hard-escalated, no fake run.
    assert "compose" not in calls and "email" not in calls
    assert summary["run_status"] == "not_built"
    assert summary["pipeline_built"] is False
    assert summary["routed_channel"] == "facebook"
    assert summary["agent_runs"] == [] and summary["runs_row"] is False
    assert "isn't built" in summary["message"]


def test_artist_with_attachments_is_honest_not_built(monkeypatch):
    calls = _spy_pipelines(monkeypatch)
    plan = CampaignPlan(
        goal="run a campaign for this artist with attachments",
        audience="all",
        channels=["instagram"],
        attach_artwork=True,
    )
    summary = _execute_campaign_sync(plan, "sess", "t", None, run_id="team-camp_x-abc")

    assert "compose" not in calls and "email" not in calls  # no fake run
    assert summary["run_status"] == "not_built"
    assert summary["routed_channel"] == "artist_artwork"


def test_summary_text_reports_not_built_honestly():
    from studio.agui import _summary_text

    txt = _summary_text(
        {"run_status": "not_built", "message": "the Facebook campaign pipeline isn't built yet"}
    )
    assert "isn't built yet" in txt
    assert "0 draft" not in txt  # never a fabricated "ran the campaign" line
