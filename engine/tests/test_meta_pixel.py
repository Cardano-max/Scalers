"""Meta Pixel audience-commonality aggregator tests (PA meeting 2026-07-11).

Pure aggregator — no network, no DB. Covers distinct-visitor share, honest zeros
on empty/signal-less feeds, config gating (no live call while disabled, token
stays a ref), and the facts-only render block.
"""

from __future__ import annotations

from studio.meta_pixel import (
    pixel_enabled,
    pixel_settings,
    render_pixel_audience_block,
    summarize_audience_commonalities,
)

_EVENTS = [
    {"visitor_id": "v1", "custom_data": {"content_category": "athleisure",
                                         "source_domain": "nike.com",
                                         "interests": ["fitness", "streetwear"]}},
    {"visitor_id": "v2", "custom_data": {"content_category": "athleisure",
                                         "source_domain": "nike.com"}},
    {"visitor_id": "v1", "custom_data": {"content_category": "athleisure"}},  # repeat visitor
    {"visitor_id": "v3", "custom_data": {"source_domain": "restaurant.com",
                                         "interests": "fitness"}},
]


def test_commonalities_count_distinct_visitors_with_honest_shares():
    s = summarize_audience_commonalities(_EVENTS)
    assert s["visitors"] == 3            # v1 counted once despite two events
    assert s["events"] == 4
    # athleisure carried by v1 + v2 -> 2/3 of visitors.
    top_cat = s["categories"][0]
    assert top_cat["value"] == "athleisure" and top_cat["count"] == 2
    assert top_cat["share"] == round(2 / 3, 4)
    # nike.com is the top referring domain (v1 + v2).
    assert s["domains"][0]["value"] == "nike.com" and s["domains"][0]["count"] == 2
    # 'fitness' shared by v1 + v3.
    fitness = next(i for i in s["interests"] if i["value"] == "fitness")
    assert fitness["count"] == 2
    assert "aggregated" in s["note"]


def test_empty_and_signalless_feeds_are_honest_zeros():
    empty = summarize_audience_commonalities([])
    assert empty["visitors"] == 0 and empty["categories"] == []
    assert "nothing to aggregate" in empty["note"]
    # Events present but no commonality fields -> honest zeros, never invented.
    silent = summarize_audience_commonalities([{"visitor_id": "v1", "custom_data": {}}])
    assert silent["visitors"] == 1
    assert silent["categories"] == [] and silent["interests"] == []
    assert "honest zeros" in silent["note"]


def test_anonymous_events_still_count_as_reach():
    s = summarize_audience_commonalities([
        {"custom_data": {"content_category": "ink"}},
        {"custom_data": {"content_category": "ink"}},
    ])
    # No visitor ids -> each event is a distinct anonymous visitor (honest reach).
    assert s["visitors"] == 2
    assert s["categories"][0]["value"] == "ink" and s["categories"][0]["count"] == 2


def test_render_block_is_facts_only_or_empty():
    assert render_pixel_audience_block(None) == ""
    assert render_pixel_audience_block(summarize_audience_commonalities([])) == ""
    block = render_pixel_audience_block(summarize_audience_commonalities(_EVENTS))
    assert "AUDIENCE COMMONALITIES" in block
    assert "nike.com" in block
    assert "not to state a claim about any one person" in block


def test_pixel_config_gate_no_live_call_and_token_stays_a_ref(monkeypatch):
    import config.loader as loader
    from config.schema import MetaPixelConfig, SecretRef, TenantPack, VoiceRef

    off = TenantPack(tenant_id="t", display_name="T", voice=VoiceRef(skill="v"))
    monkeypatch.setattr(loader, "load_pack", lambda tid, **k: off)
    assert pixel_settings("t") is None and pixel_enabled("t") is False

    on = TenantPack(
        tenant_id="t", display_name="T", voice=VoiceRef(skill="v"),
        meta_pixel=MetaPixelConfig(enabled=True, pixel_id="123",
                                   access_token=SecretRef(env="T_META_PIXEL_TOKEN")),
    )
    monkeypatch.setattr(loader, "load_pack", lambda tid, **k: on)
    s = pixel_settings("t")
    assert s is not None and s["pixel_id"] == "123"
    # The token is only reported as configured — the value is never resolved here.
    assert s["has_token"] is True and "access_token" not in s and "token" not in s
