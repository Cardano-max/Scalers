"""Competitor creative intelligence (studio/competitor_intel.py).

Pure parts (DB-free): the deterministic 0–10 scoring math — components with
absent data score None and are EXCLUDED from the renormalized weighted total
(never a fake 0/5) — niche/style tag matching, the caption deconstruction
heuristics, the upload header-shape detection, and the render block's MOLD
orders (never-copy-verbatim + traceable source url).

Postgres integration (skipif no ENGINE_DATABASE_URL, same convention as the
other *_pg tests): ingest idempotency on (tenant, url), honest metric storage
(missing columns absent, not zero-filled), persisted score breakdowns, and
``best_pattern`` returning the deconstructed top post with the LLM read
honestly skipped when no key is armed.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from studio.competitor_intel import (
    WEIGHTS,
    deconstruct_caption,
    looks_like_competitor_csv,
    render_competitor_pattern_block,
    score_components,
    weighted_total,
)

# The DB tests below carry both marks individually (this file mixes pure and
# integration tests, so no module-level pytestmark).
_pg = pytest.mark.skipif(
    not os.getenv("ENGINE_DATABASE_URL"),
    reason="requires Postgres (set ENGINE_DATABASE_URL)",
)

DSN = os.environ.get(
    "ENGINE_DATABASE_URL", "postgresql://scalers:scalers@localhost:5432/scalers"
)

_CAPTION = (
    "Would you sit 6 hours for this?\n"
    "This fine-line peony took patience and steady hands.\n"
    "Over 300 clients booked us this year.\n"
    "DM us 'PEONY' today — link in bio."
)


# --------------------------------------------------------------------------- #
# Scoring math — missing data excluded + renormalized, never faked.
# --------------------------------------------------------------------------- #
def test_missing_metrics_score_none_and_are_excluded_from_the_total():
    post = {"metrics": {"likes": 500, "views": 10000}}  # no caption/tags/date
    comp = score_components(post, [], [])
    # 500/10000 = 5% engagement → 5.0; likes are present so likes_weight scores;
    # everything else (incl. follower_reach — no follower count) has NO data → None.
    assert comp["engagement_rate"] == 5.0
    assert comp["likes_weight"] is not None  # absolute like volume is a real signal
    for key in ("follower_reach", "comments_weight", "shares_saves_weight",
                "niche_match", "style_match", "recency", "cta_strength",
                "hook_strength"):
        assert comp[key] is None
    # Renormalized over the PRESENT components only — absent data never drags the
    # total toward a fabricated 0.
    present = {k: v for k, v in comp.items() if v is not None}
    assert weighted_total(comp) == weighted_total(present)


def test_follower_reach_ranks_big_accounts_over_tiny_ones():
    from studio.competitor_intel import meets_reach_floor

    tiny = score_components({"metrics": {"likes": 100, "followers": 200}}, [], [])
    huge = score_components({"metrics": {"likes": 48000, "followers": 90000}}, [], [])
    # The 90k-follower / 48k-like account scores strictly higher on reach — the
    # exact client complaint ("it's picking up the 100-like accounts").
    assert huge["follower_reach"] > tiny["follower_reach"]
    assert huge["likes_weight"] > tiny["likes_weight"]
    assert weighted_total(huge) > weighted_total(tiny)
    # Reach is honest-None when the numbers aren't provided (never a fake 0).
    assert score_components({"metrics": {}}, [], [])["follower_reach"] is None
    # Config floor hard-excludes the tiny account but keeps the big one; an ABSENT
    # follower count still passes (we don't assume tiny from missing data).
    assert meets_reach_floor({"followers": 90000}, min_followers=5000) is True
    assert meets_reach_floor({"followers": 200}, min_followers=5000) is False
    assert meets_reach_floor({"likes": 10}, min_followers=5000) is True


def test_engagement_rate_is_honest_none_without_views():
    comp = score_components({"metrics": {"likes": 99999}}, [], [])
    assert comp["engagement_rate"] is None  # likes alone are not a rate
    comp = score_components({"metrics": {"views": 10000}}, [], [])
    assert comp["engagement_rate"] is None  # views without any interaction counts


def test_weighted_total_renormalizes_over_present_components_only():
    comp = {k: None for k in WEIGHTS}
    comp.update(engagement_rate=8.0, cta_strength=4.0)
    expect = round(
        (WEIGHTS["engagement_rate"] * 8.0 + WEIGHTS["cta_strength"] * 4.0)
        / (WEIGHTS["engagement_rate"] + WEIGHTS["cta_strength"]),
        2,
    )
    assert weighted_total(comp) == expect
    # No data at all → honest None, never a default score.
    assert weighted_total({k: None for k in WEIGHTS}) is None


def test_niche_and_style_match_use_word_token_tag_overlap():
    post = {
        "caption": "Fine line healed piece for a repeat client",
        "niche": "tattoo artist austin",
        "visual_tags": ["peony", "close-up shot"],
        "metrics": {},
    }
    comp = score_components(
        post,
        ["fine-line", "floral"],                 # our artist style tags
        ["fine-line", "peony", "blackwork"],     # our artwork library tags
    )
    # 'fine-line' word-matches 'Fine line' in the caption (artwork_select rules).
    assert comp["niche_match"] == 2.5
    # library: 'fine-line' (caption) + 'peony' (visual tag) match; blackwork not.
    assert comp["style_match"] == 5.0
    # With NO tags of ours to compare, the components are honest None, not 0.
    empty = score_components(post, [], [])
    assert empty["niche_match"] is None and empty["style_match"] is None


def test_recency_decays_deterministically_and_is_none_without_a_date():
    now = datetime(2026, 7, 10, tzinfo=timezone.utc)
    fresh = score_components({"posted_at": now, "metrics": {}}, [], [], now=now)
    stale = score_components(
        {"posted_at": now - timedelta(days=365), "metrics": {}}, [], [], now=now
    )
    assert fresh["recency"] == 10.0
    assert stale["recency"] == 0.0
    assert score_components({"metrics": {}}, [], [], now=now)["recency"] is None


def test_cta_and_hook_strength_keyword_heuristics():
    comp = score_components({"caption": _CAPTION, "metrics": {}}, [], [])
    # CTA signals: imperative ('DM'), link-in-bio, urgency ('today') → 3 × 2.5.
    assert comp["cta_strength"] == 7.5
    # Hook 'Would you sit 6 hours for this?': question (+3) + number (+2).
    assert comp["hook_strength"] == 5.0
    # No caption → honest None for both.
    none = score_components({"metrics": {}}, [], [])
    assert none["cta_strength"] is None and none["hook_strength"] is None


# --------------------------------------------------------------------------- #
# Deconstruction heuristics.
# --------------------------------------------------------------------------- #
def test_deconstruct_caption_labels_hook_context_proof_cta():
    d = deconstruct_caption(_CAPTION, ["healed close-up", "black-and-grey"])
    assert d["hook_line"] == "Would you sit 6 hours for this?"  # verbatim
    assert [s["part"] for s in d["structure"]] == ["hook", "context", "proof", "cta"]
    assert d["cta"] == "DM us 'PEONY' today — link in bio."  # verbatim trailing CTA
    assert d["emotional_angle"] == "urgency-scarcity"
    assert d["visual_pattern"] == "healed close-up, black-and-grey"


def test_deconstruct_empty_caption_is_honest_nones():
    d = deconstruct_caption("", [])
    assert d["hook_line"] is None and d["cta"] is None
    assert d["structure"] == [] and d["emotional_angle"] is None


# --------------------------------------------------------------------------- #
# Render block — the MOLD orders + traceable evidence.
# --------------------------------------------------------------------------- #
def test_render_block_orders_mold_never_copy_and_cites_the_source():
    pattern = {
        "handle": "inkrivals",
        "url": "https://instagram.com/p/abc123",
        "platform": "instagram",
        "total_score": 7.4,
        "scores": {"engagement_rate": 6.9, "recency": None},
        "why_it_worked": "Scored 7.4/10 on provided data.",
        "llm_refined": False,
        **deconstruct_caption(_CAPTION, ["healed close-up"]),
    }
    block = render_competitor_pattern_block(pattern)
    assert "NEVER copy competitor sentences verbatim" in block
    assert "structure/hook-shape/CTA-shape from this pattern" in block
    assert "artwork ONLY from our library" in block
    assert "offers ONLY substantiated codes" in block
    assert "https://instagram.com/p/abc123" in block  # traceable source
    assert "engagement_rate 6.9" in block             # score breakdown shown
    assert "recency no-data(excluded)" in block       # absent data named, not faked
    assert "hook -> context -> proof -> cta" in block


def test_render_block_is_honest_empty_without_competitor_data():
    block = render_competitor_pattern_block(None)
    assert "no competitor posts on file" in block
    assert "Do NOT invent" in block


# --------------------------------------------------------------------------- #
# Upload header-shape detection (the /studio/upload branch chain).
# --------------------------------------------------------------------------- #
def test_header_shape_detection_matches_competitor_exports_only():
    assert looks_like_competitor_csv(
        "handle,url,platform,caption,likes,comments,views,shares,saves,niche,posted_at\n"
        "@x,https://e.com/p/1,instagram,hi,1,2,3,4,5,tattoo,2026-07-01\n"
    )
    assert looks_like_competitor_csv(
        '[{"handle": "@x", "url": "https://e.com/p/1", "likes": 10}]'
    )
    # Customer lists (email/phone) and artwork CSVs are NOT competitor intel.
    assert not looks_like_competitor_csv("name,email,city\nA,a@x.com,Austin\n")
    assert not looks_like_competitor_csv("handle,email\n@x,a@x.com\n")
    assert not looks_like_competitor_csv(
        "artist,image_ref,caption,styles,motifs,collection,is_best_example\n"
    )
    assert not looks_like_competitor_csv("")


# --------------------------------------------------------------------------- #
# Postgres integration — ingest idempotency + persisted scoring + best_pattern.
# --------------------------------------------------------------------------- #
_CSV = (
    "handle,url,platform,caption,likes,comments,views,shares,saves,niche,posted_at\n"
    '@inkrivals,https://instagram.com/p/abc123,instagram,"Would you sit 6 hours for'
    ' this? Over 300 clients booked us this year. DM us today — link in bio.",'
    "1200,80,20000,15,90,tattoo fine-line,2026-07-01T12:00:00Z\n"
    '@inkrivals,https://instagram.com/p/def456,instagram,"New flash drop.",'
    "300,,,,,tattoo,\n"
)


def _seed_library(tenant: str) -> None:
    from team.store import TeamStore

    store = TeamStore(DSN)
    store.setup()
    store.record_asset(
        id=f"art_ci_{tenant}_00",
        campaign_id=f"portfolio:{tenant}",
        asset_type="studio_artwork",
        content={
            "artist": "Bella", "image_ref": "artifact://x",
            "caption": "fine-line peony", "styles": ["fine-line"],
            "motifs": ["peony"], "source": "upload",
        },
        status="library",
    )


def _cleanup(tenant: str) -> None:
    import psycopg

    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute("DELETE FROM competitor_posts WHERE tenant_id=%s", (tenant,))
        c.execute("DELETE FROM assets WHERE campaign_id=%s", (f"portfolio:{tenant}",))


@pytest.mark.integration
@_pg
def test_ingest_is_idempotent_and_never_zero_fills_metrics():
    import psycopg

    from studio.competitor_intel import ingest_competitor_csv

    tenant = "test_ci_" + uuid.uuid4().hex[:8]
    try:
        first = ingest_competitor_csv(tenant, _CSV, dsn=DSN)
        assert first["rows"] == 2 and first["ingested"] == 2
        assert first["handles"] == ["inkrivals"]

        # Re-upload: idempotent on (tenant, url) — nothing new, no duplicates.
        second = ingest_competitor_csv(tenant, _CSV, dsn=DSN)
        assert second["ingested"] == 0 and second["duplicates"] == 2

        with psycopg.connect(DSN, autocommit=True) as c:
            rows = c.execute(
                "SELECT url, metrics, posted_at FROM competitor_posts "
                "WHERE tenant_id=%s ORDER BY url",
                (tenant,),
            ).fetchall()
        assert len(rows) == 2
        full = next(r for r in rows if r[0].endswith("abc123"))
        sparse = next(r for r in rows if r[0].endswith("def456"))
        assert full[1] == {"likes": 1200, "comments": 80, "views": 20000,
                           "shares": 15, "saves": 90}
        # Missing metric columns stay ABSENT — never zero-filled as if reported.
        assert sparse[1] == {"likes": 300}
        assert full[2] is not None and sparse[2] is None  # unparseable date → NULL
    finally:
        _cleanup(tenant)


@pytest.mark.integration
@_pg
def test_score_posts_and_best_pattern_round_trip(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)  # deterministic path only
    import psycopg

    from studio.competitor_intel import (
        best_pattern,
        ingest_competitor_csv,
        score_posts,
    )

    tenant = "test_ci_" + uuid.uuid4().hex[:8]
    try:
        _seed_library(tenant)
        ingest_competitor_csv(tenant, _CSV, dsn=DSN)

        scored = score_posts(tenant, artist="Bella", dsn=DSN)
        assert len(scored) == 2
        top, other = scored[0], scored[1]
        assert top["url"].endswith("abc123")  # metrics + niche match win
        assert top["scores"]["engagement_rate"] is not None
        assert top["scores"]["niche_match"] >= 2.5  # 'fine-line' vs our style tag
        assert other["scores"]["engagement_rate"] is None  # no views provided
        assert other["scores"]["recency"] is None  # no date provided

        # Breakdown + total + rationale PERSISTED on the rows (traceable later).
        with psycopg.connect(DSN, autocommit=True) as c:
            n = c.execute(
                "SELECT count(*) FROM competitor_posts WHERE tenant_id=%s AND "
                "total_score IS NOT NULL AND scores != '{}'::jsonb AND "
                "why_it_worked IS NOT NULL",
                (tenant,),
            ).fetchone()[0]
        assert n == 2
        assert "excluded from the total" in other["why_it_worked"]

        pattern = best_pattern(tenant, artist="Bella", dsn=DSN)
        assert pattern is not None
        assert pattern["url"].endswith("abc123")
        assert pattern["hook_line"] == "Would you sit 6 hours for this?"
        assert pattern["llm_refined"] is False  # honest skip: no key armed
        block = render_competitor_pattern_block(pattern)
        assert "NEVER copy competitor sentences verbatim" in block
        assert pattern["url"] in block
    finally:
        _cleanup(tenant)


@pytest.mark.integration
@_pg
def test_best_pattern_is_honest_none_when_no_competitor_data(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from studio.competitor_intel import best_pattern

    tenant = "test_ci_empty_" + uuid.uuid4().hex[:8]
    assert best_pattern(tenant, dsn=DSN) is None
