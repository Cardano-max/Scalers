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


# --- Detector #1: follow-up-window (AC-8) -----------------------------------

def _sends(today):
    from proactive.detectors import PriorSend

    from datetime import timedelta

    return [
        PriorSend("a@x.com", today - timedelta(days=3), "camp1", name="Ana", spots_remaining=2),
        PriorSend("b@x.com", today - timedelta(days=3), "camp1", name="Bo", spots_remaining=2),
    ]


def test_follow_up_proposes_for_non_responder_in_window():
    from proactive.detectors import follow_up_opportunities

    today = date(2026, 7, 10)
    opps = follow_up_opportunities(today, prior_sends=_sends(today))
    keys = {o.key for o in opps}
    assert keys == {"follow_up:camp1:a@x.com", "follow_up:camp1:b@x.com"}
    assert all(o.kind == "follow_up" and o.source_badge == "follow_up_window" for o in opps)


def test_follow_up_excludes_opted_out_recipient():
    from proactive.detectors import follow_up_opportunities

    today = date(2026, 7, 10)
    opps = follow_up_opportunities(
        today, prior_sends=_sends(today), opted_out=frozenset({"a@x.com"})
    )
    assert {o.facts["recipient"] for o in opps} == {"b@x.com"}


def test_follow_up_excludes_responder():
    from proactive.detectors import follow_up_opportunities

    today = date(2026, 7, 10)
    opps = follow_up_opportunities(
        today, prior_sends=_sends(today), responded=frozenset({"b@x.com"})
    )
    assert {o.facts["recipient"] for o in opps} == {"a@x.com"}


def test_follow_up_excludes_already_followed_up():
    from proactive.detectors import follow_up_opportunities

    today = date(2026, 7, 10)
    opps = follow_up_opportunities(
        today, prior_sends=_sends(today), already_followed_up=frozenset({"a@x.com"})
    )
    assert {o.facts["recipient"] for o in opps} == {"b@x.com"}


def test_follow_up_window_bounds_and_spots():
    from datetime import timedelta

    from proactive.detectors import PriorSend, follow_up_opportunities

    today = date(2026, 7, 10)
    sends = [
        PriorSend("too-recent@x.com", today - timedelta(days=1), "c", spots_remaining=5),
        PriorSend("too-old@x.com", today - timedelta(days=6), "c", spots_remaining=5),
        PriorSend("full@x.com", today - timedelta(days=3), "c", spots_remaining=0),
        PriorSend("ok@x.com", today - timedelta(days=5), "c", spots_remaining=None),
    ]
    got = {o.facts["recipient"] for o in follow_up_opportunities(today, prior_sends=sends)}
    assert got == {"ok@x.com"}  # only in-window, spots-available send


# --- Detector #2: artist-special cadence (AC-2) -----------------------------

def test_artist_special_proposes_overdue_and_never_run_most_overdue_first():
    from datetime import timedelta

    from proactive.detectors import ArtistSpecial, artist_special_opportunities

    today = date(2026, 7, 10)
    artists = [
        ArtistSpecial("nikko", "Nikko", last_special_on=today - timedelta(days=40)),
        ArtistSpecial("recent", "Recent", last_special_on=today - timedelta(days=10)),
        ArtistSpecial("newbie", "Newbie", last_special_on=None),
    ]
    opps = artist_special_opportunities(today, artists=artists, cadence_days=30)
    slugs = [o.facts["artist"] for o in opps]
    assert "recent" not in slugs  # within cadence -> not due
    assert set(slugs) == {"nikko", "newbie"}
    assert slugs[0] == "nikko"  # 40d overdue ranks before never-run (cadence+1)
    assert all(o.archetype_hint == "artist_spotlight" for o in opps)
