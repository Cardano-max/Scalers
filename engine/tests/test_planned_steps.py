"""Dynamic step/agent selection (studio.interview.planned_steps / select_mode).

The supervisor plans which steps a request needs, deterministically from the plan:
a CSV/leads request runs lead analysis; a social-only request runs strategy +
critic but no per-lead analysis; opting out of deep research skips web research; a
drafts-only request skips the critic. Each step is marked selected/skipped WITH a
reason (honest — a skipped step says why, it never silently disappears).
"""

from __future__ import annotations

from studio.agui import CampaignPlan
from studio.interview import planned_steps, select_mode


def _steps_by_id(plan):
    return {s["id"]: s for s in planned_steps(plan)}


def test_provided_leads_runs_lead_analysis_and_memory():
    plan = CampaignPlan(lead_source="provided",
                        customers={"rows": 12, "columns": ["name", "email"]},
                        deep_research=True, output_count=12)
    mode, _ = select_mode(plan)
    assert mode == "personalized_outreach"
    steps = _steps_by_id(plan)
    assert steps["lead_analysis"]["selected"] is True
    assert "customer DB" in steps["lead_analysis"]["tools"]
    assert steps["web_research"]["selected"] is True   # deep research on
    assert steps["brand_voice"]["selected"] is True
    # Personalized outreach is not a multi-asset content strategy.
    assert steps["strategy"]["selected"] is False


def test_social_only_runs_strategy_and_critic_no_lead_analysis():
    plan = CampaignPlan(action_type="posts", channels=["instagram"], output_count=3)
    mode, _ = select_mode(plan)
    assert mode == "content_campaign"
    steps = _steps_by_id(plan)
    assert steps["lead_analysis"]["selected"] is False
    assert "content campaign" in steps["lead_analysis"]["reason"].lower()
    assert steps["strategy"]["selected"] is True
    assert steps["critic"]["selected"] is True


def test_opting_out_of_deep_research_skips_web_research_with_reason():
    plan = CampaignPlan(lead_source="provided", customers={"rows": 5},
                        deep_research=False)
    steps = _steps_by_id(plan)
    assert steps["web_research"]["selected"] is False
    assert "opted out" in steps["web_research"]["reason"].lower()


def test_drafts_only_skips_the_critic():
    plan = CampaignPlan(action_type="posts", output_count=2, drafts_only=True)
    steps = _steps_by_id(plan)
    # content campaign but drafts-only -> critic is skipped, with a reason.
    assert steps["critic"]["selected"] is False
    assert "drafts-only" in steps["critic"]["reason"].lower() or "single" in steps["critic"]["reason"].lower()


def test_quick_draft_mode_skips_pipeline():
    plan = CampaignPlan(drafts_only=True)  # no customers, no campaign framing
    mode, _ = select_mode(plan)
    assert mode == "quick_draft"
    steps = _steps_by_id(plan)
    assert steps["strategy"]["selected"] is False
    assert steps["jury"]["selected"] is False
    assert steps["draft"]["selected"] is True  # still drafts
    assert steps["brand_voice"]["selected"] is True


def test_performance_mode_reads_results():
    plan = CampaignPlan(action_type="results")
    mode, _ = select_mode(plan)
    assert mode == "performance"
    ids = [s["id"] for s in planned_steps(plan)]
    assert "results" in ids
    assert "draft" not in ids  # no generation pipeline


def test_review_step_always_present_and_held():
    for plan in (
        CampaignPlan(customers={"rows": 3}, deep_research=True),
        CampaignPlan(action_type="posts", output_count=2),
    ):
        steps = _steps_by_id(plan)
        assert steps["review"]["selected"] is True
        assert "approval" in steps["review"]["reason"].lower()


def test_brand_voice_step_always_loaded():
    # P1: voice grounds every generative run.
    for plan in (
        CampaignPlan(customers={"rows": 3}),
        CampaignPlan(action_type="posts", output_count=2),
        CampaignPlan(drafts_only=True),
    ):
        steps = _steps_by_id(plan)
        assert steps["brand_voice"]["selected"] is True
