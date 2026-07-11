"""Compose run-parameter tests (P1b force-research + P2 output-count) — pure/offline.

These prove the two run-shaping behaviors WITHOUT a model or DB, by exercising the
pure routing + fan-out helpers directly:

* ``_after_plan`` forces the pre-declared 'research' node ON when the plan requested
  deep research, even for an archetype (artist_spotlight) that toggles B2 off — and
  leaves the spec-driven route untouched when not forced. It NEVER invents a node.
* ``_planned_channels`` sizes the draft fan-out to the agreed ``output_count`` (round
  -robined across the spec channels, hard-capped) and falls back to the per-channel
  default when the count is unset.
"""

from __future__ import annotations

from archetypes import registry, router
from archetypes.compose import (
    CampaignState,
    _OUTPUT_HARD_CAP,
    _after_plan,
    _planned_channels,
)


def _state(archetype_id: str, **kw) -> CampaignState:
    return CampaignState(
        campaign_id="c1", run_id="r1", tenant_id="demo",
        archetype_id=archetype_id, **kw,
    )


def test_after_plan_matches_router_when_not_forced() -> None:
    # holiday enables B2 -> research; artist_spotlight/win_back do not -> strategy.
    assert _after_plan(_state("holiday")) == "research"
    assert _after_plan(_state("artist_spotlight")) == "strategy"
    assert _after_plan(_state("win_back")) == "strategy"
    # and only ever names a real spine node
    for aid in registry.ids():
        assert _after_plan(_state(aid)) in router.SPINE_NODES


def test_force_research_turns_on_research_for_a_b2_off_archetype() -> None:
    # the headline P1b fix: deep-research requested forces 'research' ON for the
    # default archetype that otherwise skips it (the "stuck queued" root cause).
    assert _after_plan(_state("artist_spotlight", force_research=True)) == "research"
    assert _after_plan(_state("win_back", force_research=True)) == "research"
    # forcing a spec that already researches is a no-op (still research)
    assert _after_plan(_state("holiday", force_research=True)) == "research"


def test_planned_channels_defaults_to_one_per_spec_channel() -> None:
    spotlight = _planned_channels(_state("artist_spotlight"))
    spec = registry.get("artist_spotlight")
    assert len(spotlight) == len(spec.channels[: spec.fanout_cap])


def test_output_count_sizes_the_fanout_round_robin() -> None:
    # 10 drafts on an email-only plan -> 10 email drafts.
    win = _planned_channels(_state("win_back", output_count=10))
    assert len(win) == 10
    # win_back channels are SMS + email -> round-robined
    assert set(win) <= {c.value for c in registry.get("win_back").channels}
    # a single-channel archetype request yields all-same-channel drafts
    assert _planned_channels(_state("win_back", output_count=4)) == [
        "sms", "email", "sms", "email",
    ]


def test_output_count_is_hard_capped() -> None:
    capped = _planned_channels(_state("artist_spotlight", output_count=9999))
    assert len(capped) == _OUTPUT_HARD_CAP
    # and never goes below 1
    assert len(_planned_channels(_state("artist_spotlight", output_count=-5))) >= 1


def test_operator_channels_constrain_the_archetype_menu():
    """'email channel, three drafts' on the win_back spec (SMS+EMAIL) must yield
    3 EMAIL drafts — a real run produced 2 SMS + 1 email for an email-only ask."""
    from archetypes.compose import CampaignState, _planned_channels

    st = CampaignState(
        campaign_id="c", run_id="r", tenant_id="t", archetype_id="win_back",
        output_count=3, plan_channels=["email"],
    )
    assert _planned_channels(st) == ["email", "email", "email"]


def test_unknown_operator_channel_falls_back_to_spec_menu():
    from archetypes.compose import CampaignState, _planned_channels

    st = CampaignState(
        campaign_id="c", run_id="r", tenant_id="t", archetype_id="win_back",
        output_count=2, plan_channels=["carrier-pigeon"],
    )
    # Empty intersection -> the spec menu still drives (never a channel-less run).
    assert len(_planned_channels(st)) == 2


def test_gmail_alias_matches_spec_email_channel():
    from archetypes.compose import CampaignState, _planned_channels

    st = CampaignState(
        campaign_id="c", run_id="r", tenant_id="t", archetype_id="win_back",
        output_count=2, plan_channels=["gmail"],
    )
    assert _planned_channels(st) == ["email", "email"]
