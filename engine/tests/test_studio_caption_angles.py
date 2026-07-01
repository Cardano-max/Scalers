"""Unit tests for the angle layer on the studio caption composer (Rec 1/2/5).

DB-free: angle selection + composition are PURE. Every test asserts BOTH that the right
angle is chosen for a style/theme AND that the composed caption still passes the
fail-closed :func:`check_caption` gate (the module's existing discipline).
"""

from __future__ import annotations

from studio.artwork_select import ArtworkPick
from studio.post_campaign import (
    PLATFORM_FACEBOOK,
    PLATFORM_INSTAGRAM,
    VoiceBundle,
    check_caption,
    compose_caption,
    pick_angle,
    theme_angle,
)


def _voice(**kw):
    base = dict(
        prefer=["made for you", "drawn for you", "your story", "take our time", "safe space"],
        ban=["slay", "boss babe", "price/discount language", "best", "walk-in / flash framing"],
        approved_claims=["Free consultation before every booking."],
        emoji_allowed=["\U0001F338"], emoji_max=2,
        hashtag_min=3, hashtag_max=6,
        example_hashtags=["flashtattoo", "womentattooartist"],
        resolved=True,
    )
    base.update(kw)
    return VoiceBundle(**base)


def _pick(styles, motifs=None, collection="", caption="A real flash piece"):
    return ArtworkPick(
        asset_id="art_csv_x",
        artist="Ladies First Flash",
        image_ref=f"flash://ladies8391/{collection or 'flash'}/x.png",
        caption=caption,
        matched_styles=list(styles),
        matched_motifs=list(motifs or []),
        score=2,
        exact_match=True,
        why="grounded",
        styles=list(styles),
        motifs=list(motifs or []),
        collection=collection,
        matched_collection=collection,
    )


# --------------------------------------------------------------------------- #
# Rec 1 — pure style -> angle
# --------------------------------------------------------------------------- #
def test_style_angle_map_is_pure_and_covers_the_taxonomy():
    assert pick_angle(_pick(["fine-line"])) == "made_for_you"
    assert pick_angle(_pick(["botanical"])) == "made_for_you"
    assert pick_angle(_pick(["traditional"])) == "artist_spotlight"
    assert pick_angle(_pick(["neo-traditional"])) == "artist_spotlight"
    assert pick_angle(_pick(["blackwork"])) == "artist_spotlight"
    assert pick_angle(_pick(["geometric"])) == "artist_spotlight"
    assert pick_angle(_pick(["script"])) == "your_words"
    assert pick_angle(_pick(["lettering"])) == "your_words"
    assert pick_angle(_pick(["cover-up"])) == "fresh_start"


def test_style_angle_defaults_to_made_for_you_when_unknown():
    assert pick_angle(_pick(["unmapped-style"])) == "made_for_you"
    assert pick_angle(None) == "made_for_you"


def test_style_angle_reads_matched_styles_before_own_styles():
    p = _pick(["fine-line"])
    p.matched_styles = ["traditional"]  # matched wins over own styles
    assert pick_angle(p) == "artist_spotlight"


# --------------------------------------------------------------------------- #
# Rec 2 — theme/collection -> seasonal angle
# --------------------------------------------------------------------------- #
def test_theme_angle_map():
    assert theme_angle("lunch-menu") == "new_flash_sheet"
    assert theme_angle("4th-of-july") == "seasonal"
    assert theme_angle("pride") == "community"
    assert theme_angle("build-a-pin") == "build_your_own"
    assert theme_angle("charm") == "build_your_own"
    assert theme_angle("floral") is None  # not a flash concept


def test_theme_angle_prefers_collection_over_theme_param():
    # The piece's own collection wins so angle + hashtag match the actual piece.
    assert theme_angle(theme="floral", collection="pride") == "community"


# --------------------------------------------------------------------------- #
# Composition per angle — chosen angle AND gate-clean, both platforms
# --------------------------------------------------------------------------- #
def _both_platforms_clean(pick, theme):
    voice = _voice()
    for plat in (PLATFORM_INSTAGRAM, PLATFORM_FACEBOOK):
        cap = compose_caption(platform=plat, artist="Ladies First Flash", pick=pick, voice=voice, theme=theme)
        assert check_caption(cap.render(), voice) == [], (plat, cap.render())
    return compose_caption(platform=PLATFORM_INSTAGRAM, artist="Ladies First Flash", pick=pick, voice=voice, theme=theme)


def test_seasonal_angle_uses_availability_language_and_is_clean():
    pick = _pick(["traditional", "neo-traditional"], ["patriotic"], "4th-of-july", "4th of July flash design")
    ig = _both_platforms_clean(pick, "4th-of-july")
    body = ig.render().lower()
    assert "new flash sheet" in body
    assert "available to book" in body or "up to book" in body
    assert "angle=seasonal" in ig.grounding
    assert "patriotictattoo" in ig.hashtags  # approved season hashtag


def test_community_angle_for_pride_and_clean():
    pick = _pick(["traditional", "illustrative"], ["pride", "rainbow"], "pride", "Pride flash design")
    ig = _both_platforms_clean(pick, "pride")
    assert "angle=community" in ig.grounding
    assert "pridetattoo" in ig.hashtags
    assert "new flash sheet" in ig.render().lower()


def test_build_your_own_angle_changes_cta_and_is_clean():
    pick = _pick(["traditional", "illustrative"], ["charm", "build-a-pin"], "build-a-pin", "Build-a-pin charm design")
    ig = _both_platforms_clean(pick, "build-a-pin")
    assert "angle=build_your_own" in ig.grounding
    assert "pick your charms" in ig.call_to_action.lower()
    assert "build your own piece" in ig.render().lower()


def test_artist_spotlight_leads_with_style_and_is_clean():
    pick = _pick(["traditional"], ["eagle"], "", "Bold traditional eagle")
    ig = _both_platforms_clean(pick, None)  # no theme -> style angle drives
    assert "angle=artist_spotlight" in ig.grounding
    assert "book this piece" in ig.call_to_action.lower()


def test_made_for_you_angle_default_and_clean():
    pick = _pick(["fine-line", "floral"], ["peony"], "", "Fine-line peony")
    ig = _both_platforms_clean(pick, None)
    assert "angle=made_for_you" in ig.grounding
    assert "start your design" in ig.call_to_action.lower()


def test_your_words_and_fresh_start_ctas_and_clean():
    lettering = _pick(["script", "lettering"], ["script"], "", "Fine-line script name")
    ig_l = _both_platforms_clean(lettering, None)
    assert "angle=your_words" in ig_l.grounding
    assert "lettering" in ig_l.call_to_action.lower()

    coverup = _pick(["cover-up", "floral"], ["bloom"], "", "Neo-traditional cover-up")
    ig_c = _both_platforms_clean(coverup, None)
    assert "angle=fresh_start" in ig_c.grounding
    assert "cover-up" in ig_c.call_to_action.lower()


# --------------------------------------------------------------------------- #
# Grounding + gate rigor
# --------------------------------------------------------------------------- #
def test_seasonal_caption_never_trips_the_flash_framing_ban():
    # 'new flash sheet' / 'flash design' are allowed; 'flash day/friday/sale' are not.
    pick = _pick(["traditional"], ["patriotic"], "4th-of-july", "4th of July flash design")
    cap = compose_caption(platform=PLATFORM_INSTAGRAM, artist="Ladies First Flash", pick=pick, voice=_voice(), theme="4th-of-july")
    # No banned framing, no fabricated scarcity/count.
    text = cap.render().lower()
    assert "flash day" not in text and "flash friday" not in text and "flash sale" not in text
    assert "only" not in text and "last chance" not in text


def test_theme_hashtag_absent_without_a_theme_angle():
    pick = _pick(["fine-line", "floral"], ["peony"], "", "Fine-line peony")
    ig = compose_caption(platform=PLATFORM_INSTAGRAM, artist="Ladies First Flash", pick=pick, voice=_voice(), theme="floral")
    assert "pridetattoo" not in ig.hashtags and "patriotictattoo" not in ig.hashtags
