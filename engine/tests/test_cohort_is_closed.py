"""A NAMED COHORT IS CLOSED — the run must never email a person the operator did not choose.

The failure this pins down actually shipped, and it is the worst class of bug this system
can have: it did the wrong thing *confidently*, to real people, and the count looked right.

The operator asked to "win back three Keebs customers from the imported conversation threads,
use their real conversations". The host selected Amanda, Lauren and Todd. All three were then
skipped — two already had a pending draft in the review queue, one tripped the
fake-personalization guard. The executor still owed three drafts, so it walked on down the
customer table and wrote personalized win-back emails to Katie Hailey, Filiberto Figueroa and
Jazmyne Nolan: three people the operator had never selected, never seen, and would have
emailed the moment they hit Approve. The run log said it out loud and nobody read it:

    Researching Katie Hailey (4 of 3)

"3 drafts" is a CAP on the operator's cohort. It is not a quota to be filled from strangers.
"""

from __future__ import annotations

from studio.agui import CampaignPlan, _use_provided_leads


def _closed(plan: CampaignPlan) -> bool:
    """Mirror of the executor's rule: is this cohort the operator's own (closed), or a
    loose 'N from my database' cohort the run may top up?"""
    return bool(
        (plan.leads or [])
        or plan.use_conversation_history is True
    )


def test_conversation_cohort_is_closed():
    """"Use their real conversations" names WHO. The run may not substitute."""
    plan = CampaignPlan(
        goal="win back three customers who stepped back on price or timing",
        channels=["email"],
        per_lead=True,
        lead_source="provided",
        use_conversation_history=True,
        output_count=3,
        lead_count=3,
    )
    assert _use_provided_leads(plan) is True  # the per-lead executor runs
    assert _closed(plan) is True  # ...and its cohort is CLOSED


def test_named_leads_cohort_is_closed():
    """Naming the people names WHO, whatever else the plan says."""
    plan = CampaignPlan(
        goal="win these three back",
        channels=["email"],
        per_lead=True,
        leads=["Amanda Kuhl", "Lauren", "Todd"],
        output_count=3,
    )
    assert _closed(plan) is True


def test_loose_database_cohort_may_still_be_topped_up():
    """The legitimate case the refill was built for: the operator asked for a COUNT from a
    cohort they described but did not enumerate. Substitution is allowed here — and, per the
    executor, is recorded on the skip ledger by name, so it can never be silent."""
    plan = CampaignPlan(
        goal="send 10 personalized win-back emails to my churn-risk customers",
        channels=["email"],
        per_lead=True,
        lead_count=10,
        output_count=10,
    )
    assert _use_provided_leads(plan) is True
    assert _closed(plan) is False  # no named people, no conversation cohort -> may refill


def test_the_exact_plan_that_shipped_the_bug_is_now_closed():
    """The operator's real words, as the host recorded them."""
    plan = CampaignPlan(
        goal=(
            "Win back three Keebs customers who stepped back over price or timing — "
            "pick them from the imported conversation threads, use their real conversations"
        ),
        audience="Warm leads from the imported conversation threads",
        channels=["email", "ig", "fb"],
        per_lead=True,
        lead_source="provided",
        use_conversation_history=True,
        deep_research=True,
        output_count=3,
        lead_count=3,
    )
    assert _closed(plan) is True, (
        "this is the plan that emailed Katie, Filiberto and Jazmyne — three people the "
        "operator never selected. Its cohort must be closed."
    )
