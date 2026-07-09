"""Example-grounded campaign generation (CustomerAcq-ju1.4).

Two lanes:
  1. hermetic — generate_campaign with the example/pattern store faked, so the whole
     generation contract is pinned offline: Angel+$1200 cites the real example ids, SMS
     drafts carry opt-out + length bounds + the honest no-send badge, no-offer copy has
     no '$', a zero-example artist is honest, the scarcity follow-up mirrors the real
     shape and never invents a spot count;
  2. @pytest.mark.integration — the LIVE keyless proof: generate for the REAL Angel and
     stage into the review queue, with the grounded example-ids visible in the action.

Anti-theater pins: offer/price only when the operator supplied it; scarcity count only
from a real ``spots`` input; SMS is honestly badged 'no-send-path'; a missing example is
stated, never fabricated.
"""

from __future__ import annotations

import json
import os

import pytest

import studio.campaign_examples_store as store
from studio.campaign_generator import (
    OPT_OUT_LINE,
    SMS_MAX_CHARS,
    SMS_MIN_CHARS,
    SMS_SEND_STATUS,
    GeneratedCampaign,
    generate_campaign,
    stage_campaign,
    summarize_patterns,
)

_DSN = (
    os.environ.get("ENGINE_DATABASE_URL")
    or os.environ.get("DATABASE_URL")
    or "postgresql://scalers:scalers@localhost:5432/scalers"
)

# Synthetic Angel pair mirroring the REAL transcribed examples (opener + scarcity follow-up).
_ANGEL_OPENER = {
    "id": "cex_angel_opener", "campaign_name": "06.18 Angel Mini App + Rev $1200",
    "follow_up_to": None, "artist_name": "Angel", "offer_price_usd": 1200,
    "cta": "Reply ANGEL to check availability or get a quote",
    "opt_out_text": "Reply STOP to opt out", "payment_plans": "Klarna & Affirm",
    "attachment_present": True, "message_chars": 413,
}
_ANGEL_FOLLOWUP = {
    "id": "cex_angel_followup", "campaign_name": "Follow-up: Angel Mini App + Rev $1200",
    "follow_up_to": "06.18 Angel Mini App + Rev $1200", "artist_name": "Angel",
    "offer_price_usd": 1200, "cta": "Text ANGEL now to claim your spot",
    "opt_out_text": "Reply STOP to opt out", "payment_plans": None,
    "attachment_present": False, "message_chars": 142,
}
_PATTERNS = [
    {"pattern_key": "artist_special", "description": "x", "evidence_example_ids": ["cex_angel_opener"], "detail": None},
    {"pattern_key": "price_anchor", "description": "x", "evidence_example_ids": ["cex_angel_opener"], "detail": {"prices": [500, 1200]}},
    {"pattern_key": "limited_spots_scarcity", "description": "x", "evidence_example_ids": ["cex_angel_followup"], "detail": None},
    {"pattern_key": "payment_plan_angle", "description": "x", "evidence_example_ids": ["cex_angel_opener"], "detail": None},
    {"pattern_key": "opener_followup_sequence", "description": "x", "evidence_example_ids": ["cex_angel_opener", "cex_angel_followup"], "detail": {"pairs": [{"opener": "cex_angel_opener", "follow_up": "cex_angel_followup"}]}},
]


@pytest.fixture
def _angel(monkeypatch):
    monkeypatch.setattr(
        store, "get_examples",
        lambda tenant, artist=None, dsn=None: (
            [_ANGEL_OPENER, _ANGEL_FOLLOWUP] if (artist or "").lower() == "angel" else []
        ),
    )
    monkeypatch.setattr(store, "get_patterns", lambda tenant, dsn=None: _PATTERNS)


def _sms(campaign: GeneratedCampaign, role: str):
    return next(d for d in campaign.drafts if d.channel == "sms" and d.role == role)


def _email(campaign: GeneratedCampaign, role: str):
    return next(d for d in campaign.drafts if d.channel == "email" and d.role == role)


# ── hermetic: the generation contract ─────────────────────────────────────────


def test_angel_1200_cites_the_real_example_ids(_angel):
    c = generate_campaign("skindesign", artist="Angel", offer_price_usd=1200,
                          payment_plan="Klarna & Affirm", spots=5)
    assert c.has_artist_examples is True
    assert set(c.grounded_example_ids) == {"cex_angel_opener", "cex_angel_followup"}
    # The opener draft cites the opener example; the follow-up cites the follow-up example.
    assert _sms(c, "opener").grounded_example_ids == ["cex_angel_opener"]
    assert _sms(c, "follow_up").grounded_example_ids == ["cex_angel_followup"]
    assert _email(c, "opener").grounded_example_ids == ["cex_angel_opener"]


def test_sms_drafts_have_opt_out_and_length_bounds(_angel):
    c = generate_campaign("skindesign", artist="Angel", offer_price_usd=1200,
                          payment_plan="Klarna & Affirm", spots=5)
    for d in [d for d in c.drafts if d.channel == "sms"]:
        assert OPT_OUT_LINE in d.body
        assert d.char_count <= SMS_MAX_CHARS
        assert SMS_MIN_CHARS <= d.char_count  # in the client's observed envelope
        assert d.send_status == SMS_SEND_STATUS  # honest: no SMS send path


def test_opener_uses_the_operator_offer_and_reply_keyword(_angel):
    c = generate_campaign("skindesign", artist="Angel", offer_price_usd=1200,
                          payment_plan="Klarna & Affirm", spots=5)
    opener = _sms(c, "opener").body
    assert "$1,200" in opener
    assert "Reply ANGEL" in opener            # mirrors the real reply-keyword CTA
    assert "Klarna & Affirm" in opener
    assert "Limited to 5 spots" in opener


def test_no_offer_run_has_no_dollar_figure(_angel):
    c = generate_campaign("skindesign", artist="Angel", offer_price_usd=None)
    for d in c.drafts:
        assert "$" not in d.body, d.body
        assert d.offer_price_usd is None


def test_scarcity_followup_mirrors_real_shape_with_real_count(_angel):
    c = generate_campaign("skindesign", artist="Angel", offer_price_usd=1200, spots=2)
    fu = _sms(c, "follow_up").body
    assert "DOWN to 2 SPOTS LEFT" in fu       # mirrors 'We are DOWN to 2 SPOTS LEFT'
    assert "Text ANGEL now" in fu
    assert OPT_OUT_LINE in fu


def test_scarcity_count_is_never_invented(_angel):
    # No spots given -> generic 'spots are limited', NEVER a fabricated number.
    c = generate_campaign("skindesign", artist="Angel", offer_price_usd=1200, spots=None)
    fu = _sms(c, "follow_up").body
    assert "spots are limited" in fu.lower()
    assert "SPOTS LEFT" not in fu
    assert not any(ch.isdigit() for ch in fu.replace("$1,200", ""))  # no invented count


def test_zero_example_artist_is_honest_never_fabricated(_angel):
    c = generate_campaign("skindesign", artist="Nobody", offer_price_usd=800)
    assert c.has_artist_examples is False
    assert c.grounded_example_ids == []
    assert any("No campaign examples on file for Nobody" in n for n in c.notes)
    assert "NO previous campaigns on file specifically for Nobody" in c.pattern_summary
    # It still generates (from tenant patterns) with the offer the operator gave.
    assert _sms(c, "opener").body and "$800" in _sms(c, "opener").body


def test_channels_selector_and_followup_toggle(_angel):
    only_sms = generate_campaign("skindesign", artist="Angel", channels=("sms",), follow_up=False)
    assert {(d.channel, d.role) for d in only_sms.drafts} == {("sms", "opener")}
    both = generate_campaign("skindesign", artist="Angel", channels=("sms", "email"))
    assert {(d.channel, d.role) for d in both.drafts} == {
        ("sms", "opener"), ("email", "opener"), ("sms", "follow_up"), ("email", "follow_up"),
    }


def test_email_variant_has_subject_and_opt_out(_angel):
    c = generate_campaign("skindesign", artist="Angel", offer_price_usd=1200)
    em = _email(c, "opener")
    assert em.subject and "Angel" in em.subject
    assert OPT_OUT_LINE in em.body
    assert em.send_status == "review-queue"


def test_summary_grounds_in_real_patterns(_angel):
    s = summarize_patterns("skindesign", "Angel")
    assert "I found 2 previous campaign(s) for Angel" in s
    assert "past specials: $500, $1,200" in s   # from the real price_anchor detail
    assert "payment-plan angle" in s


def test_long_offer_copy_is_clamped_to_sms_max(_angel):
    c = generate_campaign(
        "skindesign", artist="Angel", offer_price_usd=1200, spots=5,
        payment_plan="Klarna, Affirm, Afterpay, Sezzle and Zip, all available at checkout "
        "so you can split the full-day session however works best for your budget",
    )
    sms = _sms(c, "opener")
    assert sms.char_count <= SMS_MAX_CHARS
    assert sms.body.endswith(OPT_OUT_LINE)      # opt-out never trimmed away


# ── @integration: the live keyless proof (real Angel -> review queue) ─────────


@pytest.mark.integration
def test_live_generate_and_stage_angel_into_review_queue():
    import psycopg

    try:
        psycopg.connect(_DSN, connect_timeout=3).close()
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"no Postgres: {exc}")

    with psycopg.connect(_DSN, autocommit=True) as c:
        n = c.execute("SELECT count(*) FROM campaign_examples WHERE tenant_id='skindesign' "
                      "AND artist_name='Angel'").fetchone()[0]
    if n == 0:
        pytest.skip("skindesign Angel examples not imported")

    campaign = generate_campaign("skindesign", artist="Angel", offer_price_usd=1200,
                                 payment_plan="Klarna & Affirm", spots=5, dsn=_DSN)
    # Grounded in the 2 REAL Angel examples.
    assert len(campaign.grounded_example_ids) == 2
    assert all(i.startswith("cex_") for i in campaign.grounded_example_ids)

    run_id = "ju14-genproof-fixed"
    try:
        staged = stage_campaign(campaign, run_id=run_id, dsn=_DSN)
        assert len(staged) == len(campaign.drafts) >= 2
        # Re-stage is idempotent (same idempotency keys).
        again = stage_campaign(campaign, run_id=run_id, dsn=_DSN)
        assert again == staged

        # Example-grounding is VISIBLE in the staged action's context.
        with psycopg.connect(_DSN, autocommit=True) as c:
            rows = c.execute(
                "SELECT channel, context FROM actions WHERE run_id=%s ORDER BY channel",
                (run_id,)).fetchall()
        assert rows
        ctxs = [json.loads(ctx) for _ch, ctx in rows if ctx]
        assert any(campaign.grounded_example_ids[0] in ctx.get("grounded_example_ids", [])
                   for ctx in ctxs)
        # SMS actions are honestly badged as having no send path.
        sms_ctxs = [ctx for ch, ctx in [(r[0], json.loads(r[1])) for r in rows] if ch == "sms"]
        assert sms_ctxs and all(ctx["send_status"] == SMS_SEND_STATUS for ctx in sms_ctxs)
        assert all(ctx["no_send_path"] is True for ctx in sms_ctxs)
    finally:
        with psycopg.connect(_DSN, autocommit=True) as c:
            c.execute("DELETE FROM actions WHERE run_id=%s", (run_id,))
