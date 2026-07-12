"""Multi-channel family fixes a REAL 3-channel voice launch exposed.

The live run (email + instagram + facebook, one spoken GO):

  * the ig/fb children inherited the email leg's per-lead routing flags and ran
    the per-lead MESSAGE executor — 'Hi Kevin' recipient-addressed drafts staged
    as instagram/facebook rows, no competitor gate, no artwork pause, no image;
  * a typed 'GO AHEAD' (chat host) and a spoken '[voice GO]' launched TWO whole
    families minutes apart;
  * the merged narration numbered per-lead steps ACROSS legs ('Researching Kevin
    Koob (4 of 3)');
  * the parent's reconciliation panel read all-zero over 9 real drafts.

These tests pin the deterministic guards that make each impossible again.
"""

from __future__ import annotations

from studio.agui import (
    CampaignPlan,
    _use_provided_leads,
    _channel_overrides,
    effective_channel_plan,
    record_session_launch,
    recent_duplicate_launch,
    run_narration,
    _SESSION_LAUNCHES,
)
from studio.channel_router import Pipeline, route_pipeline
from studio.run_children import composite_status, parent_of


# --------------------------------------------------------------------------- #
# Posting children never run the per-lead message executor.
# --------------------------------------------------------------------------- #
def _multi_plan(**over) -> CampaignPlan:
    base = dict(
        goal="win back customers who stepped back over price",
        audience="past leads who stepped back due to timing or pricing",
        channels=["email", "instagram", "facebook"],
        output_count=3, lead_count=3,
        per_lead=True, lead_source="provided", use_conversation_history=True,
        deep_research=True,
    )
    base.update(over)
    return CampaignPlan(**base)


def test_posting_child_clears_per_lead_routing_fields() -> None:
    plan = _multi_plan(leads=["amanda@example.com"])
    for ch in ("instagram", "facebook"):
        eff = effective_channel_plan(plan, ch)
        assert eff.per_lead is False
        assert eff.lead_source == ""
        assert eff.use_conversation_history is False
        assert eff.leads == []
        # (The router's posting-only rule — tested below — keeps this child off
        # the email pipeline even though the winback WORDING still matches.)
    # The email child KEEPS them — that leg genuinely is per-lead.
    email = effective_channel_plan(plan, "email")
    assert email.per_lead is True and email.lead_source == "provided"
    assert _use_provided_leads(email) is True


def test_posting_child_defaults_to_one_post_not_the_email_count() -> None:
    plan = _multi_plan()
    assert effective_channel_plan(plan, "instagram").output_count == 1
    assert effective_channel_plan(plan, "fb").output_count == 1
    # An explicit per-channel count still wins.
    plan2 = _multi_plan(channel_plans={"ig": {"output_count": 4}})
    assert effective_channel_plan(plan2, "ig").output_count == 4
    # Message channels keep the shared count.
    assert effective_channel_plan(plan, "email").output_count == 3


def test_channel_plan_overrides_resolves_spoken_channel_aliases() -> None:
    plan = _multi_plan(
        channel_plans={"ig": {"image_style": "fine-line botanical",
                              "competitor_research": True}}
    )
    # The child is built from the SPOKEN token 'instagram' but the interview wrote
    # the block under 'ig' — the alias must bridge them (a real ig child read an
    # empty block exactly this way).
    assert _channel_overrides(plan, "instagram")["image_style"] == (
        "fine-line botanical"
    )
    assert _channel_overrides(plan, "ig")["competitor_research"] is True
    eff = effective_channel_plan(plan, "instagram")
    assert eff.channels == ["instagram"]


def test_router_posting_only_channels_beat_provided_lead_source() -> None:
    ig = effective_channel_plan(_multi_plan(), "instagram")
    fb = effective_channel_plan(_multi_plan(), "facebook")
    assert route_pipeline(ig).pipeline is Pipeline.INSTAGRAM
    assert route_pipeline(ig).built is True
    assert route_pipeline(fb).pipeline is Pipeline.FACEBOOK
    assert route_pipeline(fb).built is True
    # Belt over suspenders: even a RAW posting-only plan with lead_source still
    # set routes to the posting pipeline (there is no per-lead posting pipeline).
    raw = _multi_plan(channels=["instagram"])
    assert route_pipeline(raw).pipeline is Pipeline.INSTAGRAM
    # A mixed or message plan keeps the per-lead compliance path.
    assert route_pipeline(_multi_plan(channels=["email"])).pipeline is Pipeline.EMAIL


# --------------------------------------------------------------------------- #
# The winback safety net under the voice host.
# --------------------------------------------------------------------------- #
def test_winback_wording_routes_to_provided_leads_when_source_unset() -> None:
    plan = CampaignPlan(
        goal="get clients for booking",
        audience="past leads who stepped back due to timing or pricing",
        channels=["email"],
    )
    assert _use_provided_leads(plan) is True
    # An explicit non-provided source always wins over the wording net.
    plan.lead_source = "new"
    assert _use_provided_leads(plan) is False


def test_uploaded_customer_ids_route_to_provided_leads() -> None:
    plan = CampaignPlan(
        goal="promote the studio", audience="locals", channels=["email"],
        customers={"rows": 2, "customer_ids": ["cust_a", "cust_b"]},
    )
    assert _use_provided_leads(plan) is True


# --------------------------------------------------------------------------- #
# Merged narration numbers per leg, never across legs.
# --------------------------------------------------------------------------- #
def test_run_narration_numbers_within_each_channel_leg() -> None:
    def step(seq, role, channel, n_leads=None):
        out = {"lead": "Kevin Koob"}
        if n_leads is not None:
            out["n_leads"] = n_leads
        return {"seq": seq, "role": role, "channel": channel,
                "input": {}, "output": out}

    steps = []
    seq = 0
    for ch in ("email", "fb", "ig"):
        steps.append(step(seq, "strategist", ch, n_leads=3))
        seq += 1
        for _ in range(3):
            steps.append(step(seq, "researcher", ch))
            seq += 1
    lines = run_narration(steps)
    assert len(lines) == len(steps)
    researcher_lines = [
        entry for entry in lines if entry["role"] == "researcher"
    ]
    # Every leg counts 1..3 — never 4..9 across the merged family.
    for entry in researcher_lines:
        assert "4 of" not in entry["line"] and "of 9" not in entry["line"]
    # Lines carry their leg label so the panel reads unambiguously.
    assert all(entry["line"].startswith("[") for entry in researcher_lines)


# --------------------------------------------------------------------------- #
# One GO = one launch, across chat AND voice.
# --------------------------------------------------------------------------- #
def test_recent_duplicate_launch_dedupes_identical_plan_only() -> None:
    session = "sess-dedupe-test"
    _SESSION_LAUNCHES.pop(session, None)
    plan = _multi_plan()
    assert recent_duplicate_launch(session, plan) is None
    record_session_launch(
        session, plan, "team-camp_x-abc", "camp_x",
        [{"channel": "email", "runId": "team-camp_x-abc-email"}],
    )
    dup = recent_duplicate_launch(session, plan)
    assert dup is not None and dup["runId"] == "team-camp_x-abc"
    assert dup["deduped"] is True
    assert dup["children"][0]["runId"] == "team-camp_x-abc-email"
    # An EDITED plan is a new launch — never deduped.
    edited = _multi_plan(goal="a different goal")
    assert recent_duplicate_launch(session, edited) is None
    _SESSION_LAUNCHES.pop(session, None)


# --------------------------------------------------------------------------- #
# Family id helpers.
# --------------------------------------------------------------------------- #
def test_parent_of_and_composite_status() -> None:
    assert parent_of("team-camp_a-1f2e3d-ig") == "team-camp_a-1f2e3d"
    assert parent_of("team-camp_a-1f2e3d-email") == "team-camp_a-1f2e3d"
    assert parent_of("team-camp_a-1f2e3d") is None  # hex tail ≠ channel token
    assert parent_of(None) is None
    assert composite_status(["completed", "running"]) == "running"
    assert composite_status(["running", "awaiting_selection"]) == "awaiting_selection"
    assert composite_status(["completed", "error"]) == "error"
    assert composite_status(["completed", "not_built"]) == "completed"
    assert composite_status([]) == "unknown"

def test_stated_channels_never_dead_end_on_artwork_words() -> None:
    # 'attach images' phrasing set attach_artwork=true on the shared plan and the
    # EMAIL leg of a real 3-channel launch dead-ended in "standalone artist/artwork
    # pipeline isn't built" while its siblings ran. Stated channels are the
    # operator's intent — artwork words are an attribute, never a channel ask.
    email = CampaignPlan(channels=["email"], attach_artwork=True)
    assert route_pipeline(email).pipeline is Pipeline.EMAIL
    assert route_pipeline(email).built is True
    # The honest not-built survives ONLY for a channel-less artwork ask.
    bare = CampaignPlan(goal="run a campaign for this artist with attachments")
    decision = route_pipeline(bare)
    assert decision.built is False and decision.pipeline is Pipeline.ARTIST_ARTWORK
