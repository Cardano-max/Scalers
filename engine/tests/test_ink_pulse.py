"""Ink Pulse ingestion + per-customer location tests (PA meeting 2026-07-11).

The parser and location resolver are PURE (no DB, no network), so these run
hermetically. Covers: contact-required drop, verbatim conversation → notes,
ink_pulse markers, CSV + JSON shapes, header detection, and location resolution
(on-file first, never the studio city, honest-empty otherwise).
"""

from __future__ import annotations

import os
import uuid

import pytest

from studio.ink_pulse import (
    SOURCE_INK_PULSE,
    ingest_ink_pulse,
    looks_like_ink_pulse,
    parse_ink_pulse_export,
)
from studio.location import (
    location_search_query,
    resolve_customer_location,
)

_pg = pytest.mark.skipif(
    not os.environ.get("ENGINE_DATABASE_URL"),
    reason="requires Postgres (set ENGINE_DATABASE_URL)",
)

_CSV = (
    "name,email,phone,instagram,city,conversation\n"
    "Amanda Cool,amanda@x.com,555-1000,@amandacool,Austin,Loved the flash but timing was off\n"
    "Lauren,,555-2000,laurenink,Dallas,Asked about half-sleeve pricing\n"
    "NoContact,,,,Reno,just browsing\n"          # no email/phone/ig -> dropped
)

_JSON = (
    '[{"customer_name": "Todd", "email": "todd@x.com", "style": "black and grey",'
    ' "last_message": "Wants a cover-up, went quiet on price"}]'
)


def test_parse_drops_unreachable_rows_and_keeps_contactable():
    rows = parse_ink_pulse_export(_CSV)
    names = [r["name"] for r in rows]
    assert names == ["Amanda Cool", "Lauren"]        # NoContact dropped
    assert all(r["lead_stage"] == SOURCE_INK_PULSE for r in rows)
    assert all(r["source"] == SOURCE_INK_PULSE for r in rows)


def test_parse_keeps_conversation_verbatim_in_notes_and_normalizes_handle():
    rows = parse_ink_pulse_export(_CSV)
    amanda = rows[0]
    assert "Loved the flash but timing was off" in amanda["notes"]  # verbatim
    assert amanda["ig_handle"] == "amandacool"       # @ stripped
    assert amanda["location"] == "Austin"            # customer location flows through
    # A lead reachable only by phone/ig (no email) still ingests.
    lauren = rows[1]
    assert lauren["email"] == "" and lauren["phone"] == "555-2000"
    assert lauren["ig_handle"] == "laurenink"


def test_parse_json_shape_and_style_to_interests():
    rows = parse_ink_pulse_export(_JSON)
    assert len(rows) == 1
    todd = rows[0]
    assert todd["email"] == "todd@x.com"
    assert todd["interests"] == "black and grey"     # 'style' alias -> interests
    assert "went quiet on price" in todd["notes"]


def test_looks_like_ink_pulse_detects_shape():
    assert looks_like_ink_pulse(_CSV) is True
    assert looks_like_ink_pulse(_JSON) is True
    # A competitor export (handle + metrics, no contact) is NOT an ink-pulse feed.
    assert looks_like_ink_pulse("handle,url,likes,comments\n@x,u,100,5\n") is False
    assert looks_like_ink_pulse("") is False


def test_looks_like_ink_pulse_never_steals_a_plain_customer_list():
    # A generic customers CSV (contact + name, but NO consultation thread/interests)
    # must fall through to the default customers path — detection keys on the
    # consultation signal, so it is never mis-routed into the ink-pulse intake.
    assert looks_like_ink_pulse("name,email,phone,city\nA,a@x.com,555,Austin\n") is False
    # Even a generic "notes" column (ambiguous CRM note) does NOT trigger ink-pulse:
    # only an unmistakable conversation/thread/interests header does.
    assert looks_like_ink_pulse("name,email,notes\nA,a@x.com,called back\n") is False
    # But add a real consultation column and it IS an ink-pulse export.
    assert looks_like_ink_pulse("name,email,conversation\nA,a@x.com,wants a sleeve\n") is True


# -- per-customer location: on-file first, never the studio city -------------- #


def test_location_resolves_from_on_file_city_and_state():
    r = resolve_customer_location({"city": "Austin", "state": "tx"})
    assert r["city"] == "Austin" and r["state"] == "TX"
    assert r["display"] == "Austin, TX"
    assert r["source"] == "on_file" and r["confident"] is True


def test_location_parses_combined_string_from_ink_pulse():
    r = resolve_customer_location({"location": "Dallas, TX"})
    assert (r["city"], r["state"], r["confident"]) == ("Dallas", "TX", True)
    # A bare state with no city is honest — state only, not confident on city.
    bare = resolve_customer_location({"location": "TX"})
    assert bare["city"] == "" and bare["state"] == "TX" and bare["confident"] is False


def test_location_parses_space_separated_city_state():
    # A real export just as often writes "Austin TX" (no comma) — the trailing
    # 2-letter state code must still land in its own field, not the city string.
    r = resolve_customer_location({"location": "Austin TX"})
    assert (r["city"], r["state"], r["confident"]) == ("Austin", "TX", True)
    # Multi-word city, space-separated state.
    multi = resolve_customer_location({"location": "Lake Charles LA"})
    assert (multi["city"], multi["state"]) == ("Lake Charles", "LA")
    # A multi-word name whose last token is NOT a state stays intact as the city.
    ny = resolve_customer_location({"location": "New York"})
    assert (ny["city"], ny["state"]) == ("New York", "")


def test_location_string_never_clobbers_an_explicit_state():
    # A separately-provided state must survive a bare-city location string — the
    # location branch backfills, it does not overwrite ground truth.
    r = resolve_customer_location({"state": "TX", "location": "Austin"})
    assert (r["city"], r["state"]) == ("Austin", "TX")


def test_persona_inferred_city_is_not_labelled_confident():
    # A research-inferred persona city is a real signal (fills city, skips re-search)
    # but must NOT be stamped confident — only on-file ground truth is confident, so a
    # downstream copy guard can't surface a guessed city as fact.
    r = resolve_customer_location({"persona": {"city": "Reno, NV"}})
    assert r["city"] == "Reno" and r["state"] == "NV"
    assert r["source"] == "persona" and r["confident"] is False
    # …yet a search is still skipped because we already have a city.
    assert location_search_query({"persona": {"city": "Reno, NV"}}) is None


def test_location_honest_empty_when_unknown_never_defaults_to_studio():
    r = resolve_customer_location({"name": "Amanda"})
    assert r["source"] == "none" and r["confident"] is False and r["display"] == ""
    # It NEVER invents a city (the studio's or otherwise).
    assert r["city"] == ""


def test_location_search_query_built_from_consented_handles_only():
    # Resolved -> no search needed.
    assert location_search_query({"city": "Austin"}) is None
    # Unresolved but has a name + instagram -> a real, consented query.
    q = location_search_query({"name": "Amanda Cool", "ig_handle": "@amandacool"})
    assert q is not None and "Amanda Cool" in q and "amandacool" in q and "location" in q
    # Nothing to search on -> None (never a fabricated query).
    assert location_search_query({}) is None


# -- DB-backed ingest: idempotency on any handle, IG case, opt-in defaults -------- #


def _dsn() -> str:
    return os.environ["ENGINE_DATABASE_URL"]


def _fetch(tenant: str, cust_id: str) -> dict:
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(_dsn(), row_factory=dict_row) as c:
        return c.execute(
            "SELECT name,email,phone,ig_handle,city,state,source,lead_stage,"
            "email_opt_in,sms_opt_in FROM customers WHERE tenant_id=%s AND id=%s",
            (tenant, cust_id),
        ).fetchone()


@pytest.mark.integration
@_pg
def test_ingest_persists_handles_source_and_opt_ins_default_off():
    tenant = "t_inkpulse_" + uuid.uuid4().hex[:8]
    export = (
        "name,email,phone,instagram,city,conversation,interests\n"
        "Amanda,amanda@x.com,,@Amanda_Ink,Austin TX,wants a sleeve,fine-line;floral\n"
        "Lauren,,214-555-0100,,Dallas TX,memorial piece,memorial\n"
    )
    res = ingest_ink_pulse(tenant, export)
    assert res["created"] == 2 and res["matched"] == 0
    amanda = _fetch(tenant, res["customer_ids"][0])
    # phone/IG persisted (not stripped), IG lower-cased, source stamped, city/state split
    assert amanda["ig_handle"] == "amanda_ink"           # normalized lowercase
    assert amanda["source"] == SOURCE_INK_PULSE and amanda["lead_stage"] == SOURCE_INK_PULSE
    assert (amanda["city"], amanda["state"]) == ("Austin", "TX")
    # Consent OFF by default even with an email present — a quiet lead never opts itself in.
    assert amanda["email_opt_in"] is False and amanda["sms_opt_in"] is False
    lauren = _fetch(tenant, res["customer_ids"][1])
    assert lauren["phone"] == "214-555-0100" and lauren["email"] is None


@pytest.mark.integration
@_pg
def test_reingest_is_idempotent_across_handle_case_and_email_less_leads():
    tenant = "t_inkpulse_" + uuid.uuid4().hex[:8]
    first = ingest_ink_pulse(
        tenant,
        "name,instagram,conversation\nKeebs,@Keebs,wants a cover-up\n",
    )
    assert first["created"] == 1
    # Same person, DIFFERENT handle case + an email-less phone lead re-run: no dupes.
    second = ingest_ink_pulse(
        tenant,
        "name,instagram,conversation\nKeebs,keebs,still deciding\n",
    )
    assert second["created"] == 0 and second["matched"] == 1
    assert second["customer_ids"] == first["customer_ids"]  # matched the same row
