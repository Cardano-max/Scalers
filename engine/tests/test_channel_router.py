"""Intent → channel pipeline router (nmh.9, spec §16) — pure, no DB.

Proves the supervisor picks the workflow from the request: 'send emails' → email,
'create Instagram post' → IG, 'Facebook campaign' → FB (honest not-built), 'artist
with attachments' → artwork (honest not-built). The default stays email
(backward-compatible), but an explicit social intent always wins over it — the fix
for "always runs email agents".
"""

from __future__ import annotations

from dataclasses import dataclass, field

from studio.channel_router import (
    Pipeline,
    not_built_summary,
    route_pipeline,
)


@dataclass
class _Plan:
    """A minimal CampaignPlan stand-in carrying only the routed fields."""

    goal: str = ""
    audience: str = ""
    action_type: str = ""
    campaign_type: str = ""
    lead_source: str = ""
    attach_artwork: bool = False
    channels: list = field(default_factory=list)


def test_send_emails_routes_to_email():
    d = route_pipeline(_Plan(goal="send emails to lapsed clients"))
    assert d.pipeline is Pipeline.EMAIL and d.built is True


def test_create_instagram_post_routes_to_instagram_not_email():
    d = route_pipeline(_Plan(goal="create an Instagram post for the new artist"))
    assert d.pipeline is Pipeline.INSTAGRAM and d.built is True
    assert d.archetype_id == "artist_spotlight"  # a real IG archetype, not email


def test_instagram_via_channels_field_only():
    # The voice supervisor can set ONLY channels — routing must work off that alone.
    d = route_pipeline(_Plan(goal="run a campaign", channels=["instagram"]))
    assert d.pipeline is Pipeline.INSTAGRAM and d.built is True


def test_reels_and_story_route_to_instagram():
    assert route_pipeline(_Plan(goal="make a reel")).pipeline is Pipeline.INSTAGRAM
    assert route_pipeline(_Plan(goal="post an IG story")).pipeline is Pipeline.INSTAGRAM


def test_facebook_campaign_routes_not_built():
    d = route_pipeline(_Plan(goal="create a Facebook campaign"))
    assert d.pipeline is Pipeline.FACEBOOK and d.built is False
    assert "isn't built yet" in d.reason


def test_artist_with_attachments_routes_not_built():
    d = route_pipeline(_Plan(goal="run a campaign for this artist with attachments"))
    assert d.pipeline is Pipeline.ARTIST_ARTWORK and d.built is False
    assert "isn't built yet" in d.reason


def test_attach_artwork_flag_routes_to_artwork():
    d = route_pipeline(_Plan(goal="promote the studio", attach_artwork=True))
    assert d.pipeline is Pipeline.ARTIST_ARTWORK and d.built is False


def test_facebook_wins_over_email_when_both_present():
    # A mixed request naming Facebook must not silently fall through to email.
    d = route_pipeline(_Plan(goal="send a facebook campaign", channels=["email", "facebook"]))
    assert d.pipeline is Pipeline.FACEBOOK and d.built is False


def test_provided_lead_source_defaults_to_email():
    d = route_pipeline(_Plan(goal="win back clients", lead_source="provided"))
    assert d.pipeline is Pipeline.EMAIL and d.built is True


def test_provided_leads_not_bypassed_by_incidental_social_word():
    # S1: an uploaded-lead outreach plan whose goal merely MENTIONS instagram must still
    # run the per-lead email/outreach compliance path — never bypass it into an IG post.
    d = route_pipeline(
        _Plan(goal="win back clients who follow us on instagram", lead_source="provided")
    )
    assert d.pipeline is Pipeline.EMAIL and d.built is True


def test_provided_leads_facebook_still_honest_not_built():
    # But an explicitly-unbuildable channel (Facebook) stays an honest not-built even
    # with uploaded leads — we don't have an FB path to run against them.
    d = route_pipeline(_Plan(goal="facebook blast to our list", lead_source="provided"))
    assert d.pipeline is Pipeline.FACEBOOK and d.built is False


def test_no_intent_defaults_to_email_backward_compatible():
    d = route_pipeline(_Plan(goal="grow bookings"))
    assert d.pipeline is Pipeline.EMAIL and d.built is True


def test_ig_word_boundary_does_not_false_match():
    # 'ig' must not match inside 'signup' / 'design' (word-boundary guard).
    d = route_pipeline(_Plan(goal="drive signups from the design studio"))
    assert d.pipeline is Pipeline.EMAIL  # no spurious Instagram match


def test_not_built_summary_is_honest_no_fake_run():
    d = route_pipeline(_Plan(goal="facebook campaign"))
    s = not_built_summary(d, run_id="team-camp_x-y", campaign_id="camp_x")
    assert s["run_status"] == "not_built"
    assert s["pipeline_built"] is False
    assert s["agent_runs"] == [] and s["runs_row"] is False  # DB/trace: no fake run
    assert s["n_pending"] == 0
    assert "isn't built yet" in s["message"]
