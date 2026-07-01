"""Unit tests for the studio IG/FB post drafter (P2).

Covers, DB-free: voice-policy parsing, caption composition (voice-aware, platform
-distinct, grounded), the no-fabrication / brand-ban gate (clean pass AND a caught bad
string), and the staging orchestration via monkeypatched stores — exactly-once, HELD,
IG!=FB, artwork linkage, and the honest no-artwork path. The real-Postgres staging
round-trip is in ``test_studio_post_campaign_pg.py``.
"""

from __future__ import annotations

import pytest

from studio.artwork_select import ArtworkRef, artist_styles, select_artwork
from studio import post_campaign as pc
from studio.post_campaign import (
    PLATFORM_FACEBOOK,
    PLATFORM_INSTAGRAM,
    VoiceBundle,
    _parse_emoji_policy,
    _parse_hashtag_policy,
    check_caption,
    compose_caption,
    draft_studio_posts,
    resolve_voice,
)

_LADIES_EMOJI = "0-2 per caption, only \U0001F338 \U0001F337 \U0001F90D; never \U0001F525\U0001F4AF or hype emoji"
_LADIES_HASH = "3-6, lowercase, specific (#floraltattoo #austintattoo); never #inkedlife/#tattoooftheday spam"


def _ref():
    """A single portfolio piece (what list_artwork yields)."""
    return ArtworkRef(
        "art_x", "Maya", "seed://maya/peony.png", "Fine-line peony on the forearm",
        ["fine-line", "floral"], ["peony", "botanical"], True,
    )


def _pick(theme="floral"):
    """The ArtworkPick compose_caption consumes (output of the real selector)."""
    refs = [_ref()]
    return select_artwork(refs, artist_styles=artist_styles(refs), theme_terms=[theme])


def _voice(**kw):
    base = dict(
        prefer=["made for you", "drawn for you", "your story", "take our time", "safe space"],
        ban=["slay", "boss babe", "price/discount language", "best", "walk-in / flash framing"],
        approved_claims=["Free consultation before every booking.", "Woman-owned studio in Austin, TX."],
        emoji_allowed=["\U0001F338"], emoji_max=2,
        hashtag_min=3, hashtag_max=6,
        example_hashtags=["floraltattoo", "austintattoo", "womentattooartist"],
        resolved=True,
    )
    base.update(kw)
    return VoiceBundle(**base)


# --------------------------------------------------------------------------- #
# Policy parsing
# --------------------------------------------------------------------------- #
def test_parse_emoji_policy_extracts_allowed_and_cap():
    allowed, cap = _parse_emoji_policy(_LADIES_EMOJI)
    assert allowed == ["\U0001F338", "\U0001F337", "\U0001F90D"]
    assert cap == 2
    # Hype emoji after 'never' are excluded from the allow-list.
    assert "\U0001F525" not in allowed


def test_parse_hashtag_policy_bounds_and_drops_spam():
    lo, hi, ex = _parse_hashtag_policy(_LADIES_HASH)
    assert (lo, hi) == (3, 6)
    assert "floraltattoo" in ex and "austintattoo" in ex
    assert "inkedlife" not in ex and "tattoooftheday" not in ex


def test_unparseable_emoji_policy_is_conservative_zero():
    assert _parse_emoji_policy("") == ([], 0)


# --------------------------------------------------------------------------- #
# Composition
# --------------------------------------------------------------------------- #
def test_ig_and_fb_captions_are_genuinely_distinct():
    voice = _voice()
    ig = compose_caption(platform=PLATFORM_INSTAGRAM, artist="Maya", pick=_pick(), voice=voice, theme="floral")
    fb = compose_caption(platform=PLATFORM_FACEBOOK, artist="Maya", pick=_pick(), voice=voice, theme="floral")
    assert ig.render() != fb.render()
    # IG carries the allowed emoji + the fuller hashtag set; FB stays lighter.
    assert "\U0001F338" in ig.render() and "\U0001F338" not in fb.render()
    assert len(ig.hashtags) >= len(fb.hashtags)
    assert ig.call_to_action != fb.call_to_action


def test_caption_is_grounded_in_the_real_artwork_caption():
    ig = compose_caption(platform=PLATFORM_INSTAGRAM, artist="Maya", pick=_pick(), voice=_voice(), theme="floral")
    assert "fine-line peony on the forearm" in ig.render().lower()


def test_hashtags_trace_to_real_styles_motifs_or_pack_examples():
    voice = _voice()
    ig = compose_caption(platform=PLATFORM_INSTAGRAM, artist="Maya", pick=_pick(), voice=voice, theme="floral")
    allowed_roots = {"finelinetattoo", "floraltattoo", "peonytattoo", "botanicaltattoo"} | set(voice.example_hashtags)
    for h in ig.hashtags:
        assert h in allowed_roots, f"ungrounded hashtag {h!r}"


def test_free_consult_only_asserted_when_it_is_an_approved_claim():
    with_claim = compose_caption(platform=PLATFORM_INSTAGRAM, artist="Maya", pick=_pick(), voice=_voice())
    assert "consults are free" in with_claim.render().lower()
    without = compose_caption(
        platform=PLATFORM_INSTAGRAM, artist="Maya", pick=_pick(),
        voice=_voice(approved_claims=[]),
    )
    assert "free" not in without.render().lower()


def test_no_artwork_path_is_honest_and_invents_no_piece():
    cap = compose_caption(platform=PLATFORM_INSTAGRAM, artist="Noor", pick=None, voice=_voice())
    rendered = cap.render().lower()
    assert "custom noor piece" in rendered
    assert "seed://" not in rendered and "asset " not in rendered


def test_unresolved_voice_still_composes_cleanly_with_no_emoji():
    cap = compose_caption(platform=PLATFORM_INSTAGRAM, artist="Maya", pick=_pick(), voice=VoiceBundle())
    assert check_caption(cap.render(), VoiceBundle()) == []


# --------------------------------------------------------------------------- #
# The gate
# --------------------------------------------------------------------------- #
def test_gate_passes_on_composed_captions_both_platforms():
    voice = _voice()
    for plat in (PLATFORM_INSTAGRAM, PLATFORM_FACEBOOK):
        cap = compose_caption(platform=plat, artist="Maya", pick=_pick(), voice=voice, theme="floral")
        assert check_caption(cap.render(), voice) == []


def test_gate_catches_a_fabricated_hypey_caption():
    voice = _voice()
    bad = "slay queen \U0001F525 best studio in town, 20% off, only 2 spots left — book now"
    hits = check_caption(bad, voice)
    joined = " ".join(hits)
    assert "banned phrase" in joined            # slay / best
    assert "hype emoji" in joined               # 🔥
    assert "price/discount" in joined           # 20% off
    assert "scarcity" in joined                 # only 2 spots left
    assert "superlative" in joined              # best
    assert "em-dash" in joined                  # — as drama


def test_gate_flags_too_many_emoji():
    voice = _voice(emoji_max=1)
    assert any("too many emoji" in v for v in check_caption("hi \U0001F338 \U0001F337", voice))


# --------------------------------------------------------------------------- #
# Orchestration — staging via monkeypatched stores (no DB).
# --------------------------------------------------------------------------- #
class _FakeActionsStore:
    """Idempotent stand-in for actions.store: dedupes on idempotency_key like the real
    UNIQUE constraint, so we can assert exactly-once without Postgres."""

    def __init__(self):
        self.by_key: dict[str, dict] = {}

    def ensure_schema(self, dsn=None):
        return None

    def record(self, **kw):
        key = kw["idempotency_key"]
        if key in self.by_key:
            return self.by_key[key]["id"]
        aid = f"act_{len(self.by_key):04d}"
        self.by_key[key] = {"id": aid, **kw}
        return aid


@pytest.fixture
def wired(monkeypatch):
    store = _FakeActionsStore()
    monkeypatch.setattr(pc, "list_artwork", lambda tenant, artist=None, dsn=None: [_ref()])
    # record_pending_action / ensure_schema are imported INSIDE draft_studio_posts from
    # actions.store, so patch them on that module.
    from actions import store as astore

    monkeypatch.setattr(astore, "ensure_schema", store.ensure_schema)
    monkeypatch.setattr(astore, "record_pending_action", lambda **kw: store.record(**kw))
    return store


def test_stages_one_held_post_per_platform(wired):
    res = draft_studio_posts(tenant_id="ladies8391", artist_name="Maya", theme="floral")
    assert len(res["drafts"]) == 2
    kinds = {d["platform"] for d in res["drafts"]}
    assert kinds == {"instagram", "facebook"}
    for key, row in wired.by_key.items():
        assert row["type"] == "post"
        assert row["channel"] in ("instagram", "facebook")
        assert row["worker"] == "studio_post_campaign"
        assert row["esc_label"].startswith("Studio post")
        assert row["draft"]  # non-empty caption body
        # Never sets a sent/approved status — record_pending_action always writes pending.
        assert "status" not in row


def test_staging_is_exactly_once_on_rerun(wired):
    r1 = draft_studio_posts(tenant_id="ladies8391", artist_name="Maya", theme="floral")
    r2 = draft_studio_posts(tenant_id="ladies8391", artist_name="Maya", theme="floral")
    assert [d["action_id"] for d in r1["drafts"]] == [d["action_id"] for d in r2["drafts"]]
    assert len(wired.by_key) == 2  # no duplicate rows on the second run


def test_context_links_the_chosen_artwork_asset(wired):
    draft_studio_posts(tenant_id="ladies8391", artist_name="Maya", theme="floral")
    for row in wired.by_key.values():
        assert "art_x" in row["context"]  # the real asset id is traceable on the row
        assert "seed://maya/peony.png" in row["context"]


def test_ig_and_fb_rows_carry_distinct_drafts(wired):
    draft_studio_posts(tenant_id="ladies8391", artist_name="Maya", theme="floral")
    drafts = [row["draft"] for row in wired.by_key.values()]
    assert len(drafts) == 2 and drafts[0] != drafts[1]


def test_no_artwork_stages_honest_posts_without_a_fake_asset(monkeypatch, wired):
    monkeypatch.setattr(pc, "list_artwork", lambda tenant, artist=None, dsn=None: [])
    res = draft_studio_posts(tenant_id="ladies8391", artist_name="Noor")
    assert res["has_artwork"] is False and res["artwork"] is None
    for row in wired.by_key.values():
        assert "No artwork on file" in row["context"]
        assert "seed://" not in row["draft"]
