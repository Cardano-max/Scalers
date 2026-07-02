"""Unit tests for the proactive scanner's pure detectors (CustomerAcq-fr1.1).

Detectors are pure functions of (today, context) -> list[Opportunity]; no DB, no
LLM, no network. This file covers detector #3 (holiday hooks); the follow-up-window
and artist-special detectors are covered alongside their data-backed tests.
"""

from __future__ import annotations

from datetime import date


def test_federal_holiday_in_window_is_detected_with_real_date():
    from proactive.detectors import holiday_opportunities

    today = date(2026, 7, 1)  # Independence Day (Jul 4) falls 3 days out
    opps = holiday_opportunities(today, subdivisions=("NV",), window_days=21)

    july4 = [o for o in opps if o.fire_on == date(2026, 7, 4)]
    assert july4, "Independence Day should be detected in a 21-day window"
    o = july4[0]
    assert o.kind == "holiday"
    assert "Independence Day" in o.title
    assert o.source_badge.startswith("us_holidays")  # provenance = the pkg
    assert o.lead_days == 3
    assert o.key == "holiday:independence-day:2026-07-04"  # date-qualified, stable


def test_holiday_outside_window_is_not_detected():
    from proactive.detectors import holiday_opportunities

    today = date(2026, 7, 1)
    opps = holiday_opportunities(today, subdivisions=("NV",), window_days=2)
    assert all(o.fire_on <= date(2026, 7, 3) for o in opps)
    assert not [o for o in opps if o.fire_on == date(2026, 7, 4)]


def test_state_subdivision_holiday_is_badged_by_state():
    from proactive.detectors import holiday_opportunities

    # Nevada Day is a NV-only observance (last Fri of October).
    today = date(2026, 10, 20)
    nv = holiday_opportunities(today, subdivisions=("NV",), window_days=21)
    assert any("Nevada Day" in o.title for o in nv), "NV subdiv holiday expected"
    # A state with no such holiday must not surface it.
    ca = holiday_opportunities(today, subdivisions=("CA",), window_days=21)
    assert not any("Nevada Day" in o.title for o in ca)


def test_tattoo_overlay_national_tattoo_day_is_badged_overlay():
    from proactive.detectors import holiday_opportunities

    today = date(2026, 7, 1)  # National Tattoo Day = Jul 17
    opps = holiday_opportunities(today, subdivisions=("NV",), window_days=21)
    ntd = [o for o in opps if o.fire_on == date(2026, 7, 17)]
    assert ntd, "National Tattoo Day overlay expected in window"
    assert ntd[0].source_badge == "tattoo_industry_overlay"  # honest provenance
    assert "Tattoo Day" in ntd[0].title


def test_tattoo_overlay_friday_the_13th_detected():
    from proactive.detectors import holiday_opportunities

    # 2026-02-13 is a Friday.
    today = date(2026, 2, 1)
    opps = holiday_opportunities(today, subdivisions=("NV",), window_days=21)
    f13 = [o for o in opps if o.fire_on == date(2026, 2, 13)]
    assert f13, "Friday the 13th overlay expected"
    assert f13[0].source_badge == "tattoo_industry_overlay"
    assert f13[0].kind == "holiday"


def test_no_friday_13th_when_the_13th_is_not_a_friday():
    from proactive.detectors import holiday_opportunities

    # 2026-07-13 is a Monday -> no Friday-the-13th overlay.
    today = date(2026, 7, 1)
    opps = holiday_opportunities(today, subdivisions=("NV",), window_days=21)
    assert not any(
        o.fire_on == date(2026, 7, 13) and o.source_badge == "tattoo_industry_overlay"
        for o in opps
    )


def test_holiday_hint_maps_to_holiday_archetype():
    from proactive.detectors import holiday_opportunities

    today = date(2026, 7, 1)
    opps = holiday_opportunities(today, subdivisions=("NV",), window_days=21)
    assert opps and all(o.archetype_hint == "holiday" for o in opps)


def test_multi_subdivision_dedupes_shared_federal_holiday():
    from proactive.detectors import holiday_opportunities

    today = date(2026, 7, 1)
    opps = holiday_opportunities(today, subdivisions=("NV", "CA", "NY"), window_days=21)
    july4 = [o for o in opps if o.fire_on == date(2026, 7, 4)]
    assert len(july4) == 1, "a shared federal holiday must not triple-fire per state"
