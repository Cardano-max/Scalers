"""a9m.5 Draft-cell test fixtures — BUILD-READY (prep; a9m.5 not yet dispatched).

When a9m.5 is dispatched (a9m.1 ADR + a9m.3 engine half landed), drop this into
`engine/tests/` as the fixture source for `test_draft_cell.py`: the FunctionModel
payloads (PostDraft dicts) + VoiceGrounding fixtures cover the 7 cases in the design
(docs/design/a9m5-draft-cell.md §"Test plan"). Self-contained + runnable now so the
fixtures are proven correct before the build:

    python docs/design/a9m5_draft_fixtures.py    # validates every fixture; exit 0

Shapes mirror the FINAL contracts (arch PR #38 MediaSpec/PostDraft; pmm
voice-grounding-contract §1). Payloads are plain dicts (what FunctionModel returns
as the output-tool call), so they need NO schema import — wire `CopywriterDrafts`-
style: `_model(*payloads)` → `ToolCallPart(info.output_tools[0].name, payload)`.
"""

from __future__ import annotations

# --- VoiceGrounding fixtures (pmm contract §1; dimensions = the skill's emission) ---
# Representative ladies8391 dims (the real fills live in
# skills/brand-voice/tenants/ladies8391/voice-dimensions.json; emission verified ==
# reference in the brand-voice demo). Kept compact here — fixtures only need enough
# to exercise the draft cell's grounding/ban/claim paths.
_DIMS = {
    "tone": ["warm, direct, playful; first-person 'I' (Rae); soft-invite CTA"],
    "structure": ["short, one idea per line", "open on the client's story",
                  "0-2 approved emoji (🌸🌷🤍); 3-6 specific lowercase hashtags"],
    "vocabulary": {
        "prefer": ["made for you", "your story", "take our time", "cover-up", "reclaim"],
        "ban": ["unleash", "slay", "queen", "best", "#1", "painless",
                "transform your look", "in today's world",
                "AI-tells: em-dash-as-drama, rule-of-three, contrast framing"],
        "approved_claims": [
            "Woman-owned, appointment-only studio in Austin, TX.",
            "9 years tattooing, specializing in neo-traditional color and floral work.",
            "Free consultation before every booking.",
            "Custom designs drawn for you — no flash copies.",
        ],
        "emoji_policy": "0-2 per caption, only 🌸 🌷 🤍; never hype emoji",
        "hashtag_policy": "3-6, lowercase, specific; no spam walls",
    },
}


def _exemplar(content: str, sim: float) -> dict:
    return {"content": content, "metrics": {"on_voice": True}, "similarity": sim}


def voice_grounding(coverage: str) -> dict:
    """A VoiceGrounding-shaped dict (pmm §1) for the given coverage band."""
    ex = {
        "FULL": [_exemplar("She brought in her mom's garden; we drew three flowers that grew there. 🌸", 0.93),
                 _exemplar("First tattoo? We'll go slow. Free consult, no pressure.", 0.88),
                 _exemplar("Neo-traditional color lives or dies on saturation.", 0.85)],
        "PARTIAL": [_exemplar("First tattoo? We'll go slow. Free consult, no pressure.", 0.82)],
        "SPARSE": [],
    }[coverage]
    return {"tenant_id": "ladies8391", "dimensions": _DIMS, "exemplars": ex,
            "coverage": coverage, "low_grounding": coverage == "SPARSE",
            "exemplar_count": len(ex)}


# --- PostDraft FunctionModel payloads (arch PR #38 shape) ---------------------

def _media(kind, aspect=None, dur=None, brief="creative brief"):
    return {"kind": kind, "aspect_ratio": aspect, "duration_s": dur, "brief": brief}


def _draft(caption, *, hashtags=None, cta="DM me to start your design.",
           media=None, platform="instagram"):
    return {"platform": platform, "caption": caption,
            "hashtags": hashtags if hashtags is not None else
            ["neotraditionaltattoo", "floraltattoo", "austintattoo"],
            "call_to_action": cta, "media": media or _media("image", "4:5")}

# case 1 / 7 — good drafts, one per media kind
GOOD_IMAGE = _draft("She brought in her mom's garden and we drew three flowers that "
                    "actually grew there. Healed and settled now. 🌸",
                    media=_media("image", "4:5", brief="healed floral forearm, natural light"))
GOOD_REEL = _draft("A quiet look at how a custom floral piece comes together, start to finish. 🌷",
                   media=_media("reel", "9:16", 22.0, brief="timelapse of the linework session"))
GOOD_CAROUSEL = _draft("Three pieces I drew for women reclaiming a scar this month.",
                       media=_media("carousel", "4:5", brief="3-slide healed gallery"))
GOOD_TEXT = _draft("Booked through spring. Consults are open for summer now.",
                   hashtags=["austintattoo", "womentattooartist"],
                   media=_media("text"))

# case 2 — AI-slop draft (then repaired with GOOD_IMAGE)
SLOP = _draft("In today's world, it's not just a tattoo — it's a statement. "
              "Bold, beautiful, timeless.")
# case 3 — over-length caption + hashtag wall
OVERLONG = _draft("word " * 700, hashtags=[f"tag{i}" for i in range(20)])
# case 4 — incoherent media (reel without duration; duration out of 5-90)
BAD_REEL_NO_DURATION = _draft("Reel with no duration.", media=_media("reel", "9:16", None))
BAD_REEL_LONG = _draft("Reel too long.", media=_media("reel", "9:16", 150.0))
# case 6 — claim not in approved_claims (+ pain promise)
UNAPPROVED_CLAIM = _draft("100% painless, guaranteed — best floral work in Austin.")

# Repair pairs: (broken_first, fixed_second) for run_detailed_sync repair tests.
REPAIR_SLOP = (SLOP, GOOD_IMAGE)
REPAIR_CLAIM = (UNAPPROVED_CLAIM, GOOD_IMAGE)

CASES = {
    "good_image": GOOD_IMAGE, "good_reel": GOOD_REEL, "good_carousel": GOOD_CAROUSEL,
    "good_text": GOOD_TEXT, "slop": SLOP, "overlong": OVERLONG,
    "bad_reel_no_duration": BAD_REEL_NO_DURATION, "bad_reel_long": BAD_REEL_LONG,
    "unapproved_claim": UNAPPROVED_CLAIM,
}


# --- self-check (prove the fixtures are correct before a9m.5 wires them) ------

def _media_coherent(m: dict) -> bool:
    k = m["kind"]
    if k == "reel":
        return m["aspect_ratio"] == "9:16" and isinstance(m["duration_s"], (int, float)) \
            and 5 <= m["duration_s"] <= 90
    if k in ("image", "carousel"):
        return bool(m["aspect_ratio"]) and m["duration_s"] is None
    if k == "text":
        return m["aspect_ratio"] is None and m["duration_s"] is None
    return False


def _self_check() -> None:
    # grounding bands
    assert voice_grounding("FULL")["exemplar_count"] == 3
    assert voice_grounding("SPARSE")["exemplars"] == [] and voice_grounding("SPARSE")["low_grounding"]
    assert voice_grounding("PARTIAL")["low_grounding"] is False
    for c in ("FULL", "PARTIAL", "SPARSE"):
        d = voice_grounding(c)["dimensions"]
        assert {"tone", "structure", "vocabulary"} <= set(d)
        assert {"prefer", "ban", "approved_claims", "emoji_policy", "hashtag_policy"} <= set(d["vocabulary"])

    # good drafts: coherent media, on-voice (no banned token, no AI-tell marker)
    tells = ["in today's world", "it's not just", "—", "bold, beautiful, timeless", "painless", "best"]
    for name in ("good_image", "good_reel", "good_carousel", "good_text"):
        dft = CASES[name]
        assert _media_coherent(dft["media"]), f"{name}: incoherent media"
        low = dft["caption"].lower()
        assert not any(t in low for t in tells), f"{name}: unexpected tell"
        assert 3 <= len(dft["hashtags"]) <= 6 or dft["media"]["kind"] == "text"

    # bad drafts trip exactly what they should
    assert "in today's world" in SLOP["caption"].lower() and "—" in SLOP["caption"]
    assert len(OVERLONG["caption"].split()) > 120 and len(OVERLONG["hashtags"]) > 6
    assert not _media_coherent(BAD_REEL_NO_DURATION["media"])
    assert not _media_coherent(BAD_REEL_LONG["media"])
    assert "painless" in UNAPPROVED_CLAIM["caption"].lower()
    # the unapproved claim is genuinely absent from approved_claims
    approved = " ".join(_DIMS["vocabulary"]["approved_claims"]).lower()
    assert "painless" not in approved and "best floral work" not in approved

    print("a9m.5 fixtures self-check: OK")
    print(f"  grounding bands: FULL/PARTIAL/SPARSE | drafts: {len(CASES)} cases "
          f"(4 good media kinds + slop + overlong + 2 bad-media + unapproved-claim)")
    print("  repair pairs: REPAIR_SLOP, REPAIR_CLAIM (broken -> fixed)")


if __name__ == "__main__":
    _self_check()
