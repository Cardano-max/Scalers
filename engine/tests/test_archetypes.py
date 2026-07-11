"""Phase-A dynamic-workflow tests — pure/offline (no model, no DB).

These prove the SAFETY properties by construction: the model can never pick
topology, every spec composes the approve-first core, the router only ever names a
pre-declared spine node, and the classifier's output is Enum-bounded to the
registry. The live proofs (real classify model call + a composed DB run) are run
separately (see the task's VERIFY steps), not here.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from archetypes import registry, router
from archetypes.registry import ArchetypeId
from archetypes.spec import ArchetypeSpec, Channel, GateSet, StepKind, TriggerClass


def test_registry_has_the_anchor_types():
    assert set(registry.ids()) == {
        "artist_spotlight", "holiday", "win_back", "facebook_post",
    }
    for spec in registry.REGISTRY.values():
        assert isinstance(spec, ArchetypeSpec)


def test_facebook_post_spec_is_registered_with_a_valid_spine_path():
    # The FB page-post archetype the channel router pins: FB-first channels, the
    # approve-first core, and a spine path made only of pre-declared nodes
    # (plan -> strategy -> draft fan-out -> critique -> route -> queue; B2 off).
    spec = registry.get("facebook_post")
    assert spec.channels[0] is Channel.FB and Channel.FB.value == "fb"
    assert Channel.EMAIL in spec.channels
    assert spec.gates.approval_tier == "hold"
    assert spec.gates.consent_required is False  # organic page feed, never DMs
    path = router.enabled_path(spec)
    assert set(path) <= router.SPINE_NODES
    assert "research" not in path  # B2 off by default (force_research still works)
    for node in ("plan", "strategy", "draft_dispatch", "draft_one", "critique",
                 "route", "queue"):
        assert node in path


def test_every_spec_composes_the_approve_first_core():
    # route + hold + publish are structurally required — the gate is not optional.
    core = {StepKind.B10_ROUTE, StepKind.B11_HOLD, StepKind.B14_PUBLISH}
    for spec in registry.REGISTRY.values():
        assert core <= spec.steps_enabled


def test_spec_rejects_missing_core_block():
    with pytest.raises(ValidationError):
        ArchetypeSpec(
            id="bad", trigger=TriggerClass.OPERATOR,
            steps_enabled={StepKind.B6_STRATEGY},  # no route/hold/publish
            channels=[Channel.IG], rubric_id="r", success_metric="x",
        )


def test_gateset_phase_a_must_hold():
    with pytest.raises(ValidationError):
        GateSet(approval_tier="auto")
    assert GateSet().approval_tier == "hold"


def test_router_selects_only_predeclared_nodes():
    # The model cannot add a node: every routed node is in the frozen spine set.
    assert router.selects_only_predeclared_nodes() is True
    for spec in registry.REGISTRY.values():
        for node in router.enabled_path(spec):
            assert node in router.SPINE_NODES


def test_b2_toggle_is_honored_by_the_router():
    # holiday enables B2 research; artist_spotlight does not. The router routes
    # past research only when B2 is off — pure data, no model.
    holiday = router.enabled_path(registry.get("holiday"))
    spotlight = router.enabled_path(registry.get("artist_spotlight"))
    assert "research" in holiday
    assert "research" not in spotlight
    # plan -> research for holiday, plan -> strategy for spotlight
    assert router.route_archetype({"archetype_id": "holiday"}, after="plan") == "research"
    assert router.route_archetype({"archetype_id": "artist_spotlight"}, after="plan") == "strategy"


def test_router_rejects_unregistered_archetype():
    with pytest.raises(KeyError):
        router.route_archetype({"archetype_id": "not_a_real_type"}, after="plan")


def test_fanout_cap_bounds_channels():
    for spec in registry.REGISTRY.values():
        assert 1 <= spec.fanout_cap <= 12
        # the dispatcher will only fan out up to fanout_cap channels
        assert len(spec.channels[: spec.fanout_cap]) <= spec.fanout_cap


def test_classifier_output_is_enum_bounded_to_registry():
    # The classifier's structural output type is the dynamic registry Enum, so an
    # unregistered id cannot be constructed — the model can never invent a type.
    from archetypes.classify import ArchetypeChoice

    ok = ArchetypeChoice(archetype_id="artist_spotlight", rationale="new artist to promote")
    assert ok.id == "artist_spotlight"
    with pytest.raises(ValidationError):
        ArchetypeChoice(archetype_id="made_up_type", rationale="nope")
    # every registered id round-trips through the Enum
    assert {e.value for e in ArchetypeId} == set(registry.ids())


def test_campaign_state_assets_have_additive_reducer():
    # fan-in safety: parallel draft_one workers must accumulate, not clobber.
    import operator

    from archetypes.compose import CampaignState

    meta = CampaignState.model_fields["assets"].metadata
    assert operator.add in meta, "assets must carry operator.add for Send fan-in"
