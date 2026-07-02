"""Campaign Example Library (SD-MEMORY, CustomerAcq-ju1.2).

Three lanes (ju1.1 convention):
  1. synthetic always-run — pure parse + deterministic pattern extraction, DB-free;
  2. ``@needs_real_file`` — shape of the REAL transcribed client file (gitignored PII);
  3. ``@pytest.mark.integration`` — Postgres roundtrip: idempotent double-ingest +
     retrieval, throwaway tenant, cleaned up in ``finally``.

Honesty pins: every stored example is badged ``source='operator_screenshot'`` (it was
transcribed, not invented); a field not visible in the screenshot stays null (never
inferred); an unknown JSON field is REPORTED, never silently dropped; a pattern row
exists ONLY with non-empty evidence example ids; an artist with no examples reads as
``[]``, never a fabricated example.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from studio.campaign_examples_store import (
    SOURCE_OPERATOR_SCREENSHOT,
    example_id,
    extract_patterns,
    get_examples,
    get_patterns,
    import_campaign_examples,
    parse_examples_json,
)

CLIENT_DATA = Path("C:/Users/Links/Desktop/CustomerAcq/client-data")
REAL_EXAMPLES = CLIENT_DATA / "campaign-examples.json"

needs_real_file = pytest.mark.skipif(
    not REAL_EXAMPLES.exists(), reason="client-data/ (gitignored PII) not present"
)

# ── synthetic fixture: 3 campaigns exercising every detector + null/unknown paths ──

_SYNTHETIC = {
    "_provenance": {"source": "unit-test synthetic screenshots", "extraction": "synthetic"},
    "campaigns": [
        {
            "source_screenshot": "TEST1.png",
            "campaign_name": "07.01 Maya Flash $800",
            "status": "Sent",
            "sent_at": "2026-07-01 20:00 GMT+5",
            "artist_name": "Maya",
            "offer_price_usd": 800,
            "offer_type": "full-day special, limited 3 spots",
            "recipient_count": 200,
            "delivered_count": 150,
            "sent_pending_count": 0,
            "failed_count": 20,
            "dnd_blocked_count": 30,
            "message_copy": (
                "MAYA FULL-DAY SPECIAL\n\nFor a limited 3 spots, full-day sessions at "
                "$800.\n\nKlarna & Affirm payment plans available.\n\nReply MAYA to "
                "check availability\nReply STOP to opt out"
            ),
            "message_chars": 170,
            "cta": "Reply MAYA to check availability",
            "opt_out_text": "Reply STOP to opt out",
            "payment_plans": "Klarna & Affirm",
            "attachment_present": True,
            "attachment_note": "1 artwork image",
            "categories": ["Mini App"],
            "location": "Test Studio Spring Mountain",
            "wizz": 1,  # unknown field -> must be REPORTED, never silently dropped
        },
        {
            "source_screenshot": "TEST2.png",
            "campaign_name": "Follow-up: 07.01 Maya Flash $800",
            "follow_up_to": "07.01 Maya Flash $800",
            "status": "Sent",
            "sent_at": "2026-07-02 21:00 GMT+5",
            "artist_name": "Maya",
            "offer_price_usd": 800,
            "offer_type": "scarcity follow-up",
            "recipient_count": 200,
            "delivered_count": 160,
            "failed_count": 15,
            "dnd_blocked_count": 25,
            "message_copy": (
                "DOWN to 1 SPOT LEFT for Maya's $800 FULL DAY SPECIAL\n\nText MAYA now"
                "\n\nReply STOP to opt out"
            ),
            "cta": "Text MAYA now",
            "opt_out_text": "Reply STOP to opt out",
            "attachment_present": False,
            "categories": [],
            "artists_selected": ["Maya"],
            "location": None,
        },
        {
            "source_screenshot": "TEST3.png",
            "campaign_name": "Follow-up: 06.30 Rio promo",
            "follow_up_to": "06.30 Rio promo",  # opener NOT in the set -> unpaired
            "status": "Sent",
            "sent_at": "2026-07-02 22:00 GMT+5",
            "artist_name": "Rio",
            "offer_price_usd": None,
            "offer_type": "personal-outreach framing (no price stated)",
            "recipient_count": 100,
            "delivered_count": 80,
            "failed_count": 10,
            "dnd_blocked_count": 10,
            "message_copy": (
                "Hey! Rio wanted me to personally reach out to you about her promo?"
                "\n\nReply 'stop' to opt out"
            ),
            "cta": "implicit reply-to-express-interest",
            "opt_out_text": "Reply 'stop' to opt out",
            "attachment_present": False,
            "categories": [],
        },
    ],
}


def _parsed(tenant: str = "t_syn"):
    res = parse_examples_json(json.dumps(_SYNTHETIC))
    for ex in res["examples"]:
        ex["id"] = example_id(tenant, ex["campaign_name"])
    return res


def _pattern(patterns: list[dict], key: str) -> dict | None:
    return next((p for p in patterns if p["pattern_key"] == key), None)


# ── lane 1: parse (pure) ──────────────────────────────────────────────────────


def test_parse_badges_every_example_as_transcribed():
    res = _parsed()
    assert len(res["examples"]) == 3
    assert all(ex["source"] == SOURCE_OPERATOR_SCREENSHOT for ex in res["examples"])
    assert res["provenance"]["extraction"] == "synthetic"


def test_parse_preserves_nulls_and_never_infers():
    e1, e2, e3 = _parsed()["examples"]
    # Missing key and explicit null both stay None — never inferred.
    assert e1["follow_up_to"] is None and e1["from_number"] is None
    assert e2["sent_pending_count"] is None and e2["message_chars"] is None
    assert e2["location"] is None
    assert e3["offer_price_usd"] is None and e3["artists_selected"] is None
    # A visible-but-empty list stays [] (distinct from not-visible None).
    assert e2["categories"] == [] and e1["categories"] == ["Mini App"]


def test_parse_reports_unknown_fields_drops_nothing_silently():
    summary = _parsed()["summary"]
    assert summary["examples_seen"] == 3 and summary["parsed"] == 3
    assert summary["unknown_fields"] == {"07.01 Maya Flash $800": ["wizz"]}


def test_parse_skips_unkeyable_entry_with_reason():
    doc = {"campaigns": [{"artist_name": "NoName"}]}
    res = parse_examples_json(json.dumps(doc))
    assert res["examples"] == []
    assert res["summary"]["skipped"] == [{"index": 0, "reason": "no campaign_name"}]


def test_example_id_is_deterministic_and_tenant_scoped():
    a = example_id("skindesign", "06.18 Angel Mini App + Rev $1200")
    assert a == example_id("skindesign", "06.18 Angel Mini App + Rev $1200")
    assert a != example_id("other", "06.18 Angel Mini App + Rev $1200")
    assert a.startswith("cex_") and len(a) == 20


# ── lane 1: pattern extraction (pure, deterministic, keyless) ─────────────────


def test_patterns_are_deterministic_and_every_row_has_evidence():
    examples = _parsed()["examples"]
    p1, p2 = extract_patterns(examples), extract_patterns(examples)
    assert p1 == p2  # same input -> byte-identical patterns (keyless, no model)
    assert p1 == sorted(p1, key=lambda p: p["pattern_key"])
    ids = {ex["id"] for ex in examples}
    for p in p1:
        assert p["evidence_example_ids"], f"{p['pattern_key']} emitted with no evidence"
        assert set(p["evidence_example_ids"]) <= ids


def test_detectors_link_the_right_evidence():
    examples = _parsed()["examples"]
    id1, id2, id3 = (ex["id"] for ex in examples)
    got = {p["pattern_key"]: p for p in extract_patterns(examples)}

    assert got["artist_special"]["evidence_example_ids"] == [id1, id2]
    assert got["price_anchor"]["evidence_example_ids"] == [id1, id2]
    assert got["price_anchor"]["detail"]["prices"] == [800]
    assert got["limited_spots_scarcity"]["evidence_example_ids"] == [id1, id2]
    assert got["reply_artist_cta"]["evidence_example_ids"] == [id1, id2]
    assert got["payment_plan_angle"]["evidence_example_ids"] == [id1]
    assert got["personal_outreach_framing"]["evidence_example_ids"] == [id3]
    assert got["artwork_attachment_on_opener"]["evidence_example_ids"] == [id1]
    assert got["stop_opt_out"]["evidence_example_ids"] == [id1, id2, id3]
    assert got["category_location_targeting"]["evidence_example_ids"] == [id1, id2]

    seq = got["opener_followup_sequence"]
    assert seq["evidence_example_ids"] == [id1, id2, id3]
    assert seq["detail"]["pairs"] == [{"opener": id1, "follow_up": id2}]
    assert seq["detail"]["unpaired_follow_ups"] == [id3]


def test_delivery_reality_is_computed_from_counts_not_prose():
    examples = _parsed()["examples"]
    dr = _pattern(extract_patterns(examples), "delivery_reality")
    assert dr is not None
    # 150/200=75.0, 160/200=80.0, 80/100=80.0 ; failed 10.0/7.5/10.0 ; dnd 15.0/12.5/10.0
    assert dr["detail"]["delivered_pct"] == {"min": 75.0, "max": 80.0}
    assert dr["detail"]["failed_pct"] == {"min": 7.5, "max": 10.0}
    assert dr["detail"]["dnd_blocked_pct"] == {"min": 10.0, "max": 15.0}
    assert len(dr["evidence_example_ids"]) == 3


def test_pattern_with_no_evidence_is_absent_never_fabricated():
    # Only the personal-outreach example -> most detectors have no evidence and
    # must be ABSENT (a pattern row is never emitted on zero evidence).
    examples = [ex for ex in _parsed()["examples"] if ex["artist_name"] == "Rio"]
    keys = {p["pattern_key"] for p in extract_patterns(examples)}
    assert "price_anchor" not in keys and "payment_plan_angle" not in keys
    assert "artwork_attachment_on_opener" not in keys
    assert "personal_outreach_framing" in keys and "stop_opt_out" in keys


def test_extract_patterns_empty_input_is_empty():
    assert extract_patterns([]) == []


# ── lane 2: the REAL transcribed client file (skipif-missing PII) ─────────────


@needs_real_file
def test_real_file_parses_5_examples_with_provenance():
    res = parse_examples_json(REAL_EXAMPLES.read_text(encoding="utf-8"))
    assert res["summary"]["examples_seen"] == 5 and res["summary"]["parsed"] == 5
    assert res["summary"]["unknown_fields"] == {}  # every field is a known column
    assert "screenshots" in (res["provenance"]["source"] or "")
    names = [ex["artist_name"] for ex in res["examples"]]
    assert names.count("Angel") == 2  # opener + scarcity follow-up
    assert {"Bella", "Lynn", "Keebs"} <= set(names)
    assert all(ex["source"] == SOURCE_OPERATOR_SCREENSHOT for ex in res["examples"])
    # Lynn's price was not visible in the screenshot -> stays null, never inferred.
    lynn = next(ex for ex in res["examples"] if ex["artist_name"] == "Lynn")
    assert lynn["offer_price_usd"] is None


@needs_real_file
def test_real_file_patterns_cite_real_example_ids():
    res = parse_examples_json(REAL_EXAMPLES.read_text(encoding="utf-8"))
    for ex in res["examples"]:
        ex["id"] = example_id("skindesign", ex["campaign_name"])
    by_name = {ex["campaign_name"]: ex["id"] for ex in res["examples"]}
    got = {p["pattern_key"]: p for p in extract_patterns(res["examples"])}

    ids = set(by_name.values())
    for p in got.values():
        assert set(p["evidence_example_ids"]) <= ids

    # The Angel opener+follow-up sequence is detected as a real PAIR.
    pairs = got["opener_followup_sequence"]["detail"]["pairs"]
    assert {"opener": by_name["06.18 Angel Mini App + Rev $1200"],
            "follow_up": by_name["Follow-up: Angel Mini App + Rev $1200"]} in pairs
    # reply-{ARTIST} CTA: both Angel sends + the Keebs follow-up.
    assert len(got["reply_artist_cta"]["evidence_example_ids"]) == 3
    # Lynn's personal-outreach framing is exactly her send.
    lynn_id = next(i for n, i in by_name.items() if "LYNN" in n.upper())
    assert got["personal_outreach_framing"]["evidence_example_ids"] == [lynn_id]
    # Price anchors are the real ones from the sends.
    assert got["price_anchor"]["detail"]["prices"] == [500, 1200]
    # Every send carries STOP opt-out; delivery reality spans all 5.
    assert len(got["stop_opt_out"]["evidence_example_ids"]) == 5
    dr = got["delivery_reality"]["detail"]
    assert 60 < dr["delivered_pct"]["min"] < dr["delivered_pct"]["max"] < 90
    assert dr["dnd_blocked_pct"]["max"] > 20  # the 21% DND reality is visible


# ── lane 3: Postgres roundtrip (idempotent ingest + retrieval API) ────────────


@pytest.mark.integration
@needs_real_file
def test_import_is_idempotent_and_retrieval_matches_ac(tmp_path):
    dsn = os.environ.get("ENGINE_DATABASE_URL") or "postgresql://scalers:scalers@localhost:5432/scalers"
    tenant = "t_sdexamples"
    import psycopg

    try:
        r1 = import_campaign_examples(REAL_EXAMPLES, tenant, dsn=dsn)
        assert r1["created"] == 5 and r1["updated"] == 0
        assert r1["patterns"] >= 10

        # Re-ingest is a full no-op on rows: nothing duplicated, ids stable.
        r2 = import_campaign_examples(REAL_EXAMPLES, tenant, dsn=dsn)
        assert r2["created"] == 0 and r2["updated"] == 5

        rows = get_examples(tenant, dsn=dsn)
        assert len(rows) == 5
        assert all(r["source"] == SOURCE_OPERATOR_SCREENSHOT for r in rows)

        # AC: get_examples(artist='Angel') -> 2 (opener + follow-up), case-insensitive.
        angel = get_examples(tenant, artist="Angel", dsn=dsn)
        assert len(angel) == 2
        assert {a["follow_up_to"] is None for a in angel} == {True, False}
        assert len(get_examples(tenant, artist="angel", dsn=dsn)) == 2

        # AC edge: an artist with no examples -> [], never a fabricated example.
        assert get_examples(tenant, artist="NoSuchArtist", dsn=dsn) == []
        assert get_examples("t_never_ingested", dsn=dsn) == []

        # AC: patterns reference REAL example ids of this tenant.
        pats = get_patterns(tenant, dsn=dsn)
        ids = {r["id"] for r in rows}
        assert pats and all(set(p["evidence_example_ids"]) <= ids for p in pats)
        assert all(p["evidence_example_ids"] for p in pats)
        assert get_patterns("t_never_ingested", dsn=dsn) == []
    finally:
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute("DELETE FROM campaign_example_patterns WHERE tenant_id = %s", (tenant,))
            conn.execute("DELETE FROM campaign_examples WHERE tenant_id = %s", (tenant,))
