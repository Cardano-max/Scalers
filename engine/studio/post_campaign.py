"""Studio IG/FB post DRAFTING — per-artist caption + artwork pick, staged HELD (P2).

The studio personalization layer that sits ON TOP of generic post-drafting. For one
seeded/real artist it produces, per platform:

  * a caption in the studio's own BRAND VOICE (``resolve_brand_voice`` /
    ``VoiceDimensions``) — it draws its opener + phrasing from the pack's *preferred
    lexicon*, obeys the pack's *emoji* + *hashtag* policy, and is GATED against the
    pack's *hard bans* + a structural no-fabrication gate (no price/discount, no
    scarcity, no superlatives, no invented engagement, no hype emoji, no em-dash
    drama); and
  * an artwork PICK from the studio's real portfolio (:mod:`studio.artwork_select`)
    with an evidence-grounded "which artwork & why" that traces to stored asset
    metadata only.

Both are staged as HELD ``actions`` rows (``type="post"``, ``channel=instagram|facebook``,
``status='pending'``) via :func:`actions.store.record_pending_action`. NOTHING publishes:
real IG/FB publishing is P4 (blocked on Meta App Review). Staging is EXACTLY-ONCE — the
idempotency_key is deterministic for a logical (tenant, artist, theme, platform), so a
re-run returns the SAME action ids instead of duplicating.

Honesty gates:
  * The caption is grounded ONLY in the artist name, the picked artwork's real
    caption/styles/motifs, the pack's approved lexicon, and studio-wide approved claims
    (e.g. free consultation). It asserts nothing specific it cannot substantiate.
  * If the artist has no artwork on file the pick is honestly ``None`` and the caption
    says the studio would attach a piece on approval — it does not invent a picture.

This module owns a STANDALONE entrypoint (``python -m studio.post_campaign``); the
supervisor wires it into the agui run loop separately. It does NOT touch
``studio/agui.py`` or any ``cells/post_*.py``.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

from studio.artwork_select import (
    ArtworkPick,
    artist_styles as _artist_styles,
    list_artwork,
    select_artwork,
)

_DEFAULT_TENANT = "ladies8391"
_DEFAULT_DSN = "postgresql://scalers:scalers@localhost:5432/scalers"

# Platforms this slice drafts for (both map 1:1 to actions.channel).
PLATFORM_INSTAGRAM = "instagram"
PLATFORM_FACEBOOK = "facebook"
DEFAULT_PLATFORMS = (PLATFORM_INSTAGRAM, PLATFORM_FACEBOOK)

# Emoji unicode ranges (broad) — for parsing the pack's emoji policy + counting output.
_EMOJI_RE = re.compile(
    "[" "\U0001F300-\U0001FAFF" "\U00002600-\U000027BF" "\U0001F1E6-\U0001F1FF"
    "\U00002190-\U000021FF" "\U0000FE00-\U0000FE0F" "]",
    flags=re.UNICODE,
)

# Hype emoji that must never appear (mirrors the pack's "never …" list; enforced even
# if a pack fails to resolve).
_HYPE_EMOJI = set("🔥💯✨👑🙌⚡️💥🚀")

# Structural no-fabrication tripwires (word-boundary), independent of the pack so the
# gate holds even when the voice pack cannot be resolved.
_DISCOUNT_RE = re.compile(r"\b(price|priced|pricing|discount|discounted|sale|deal|deals|coupon|promo|percent)\b|%|\$", re.I)
_SCARCITY_RE = re.compile(r"\b(only\s+\d+|\d+\s+(spots?|slots?|left)|last\s+chance|hurry|limited\s+time|selling\s+fast|book\s+now\s+before)\b", re.I)
_SUPERLATIVE_RE = re.compile(r"\b(best|#1|number\s+one|world[- ]class|the\s+finest|unbeatable|greatest)\b", re.I)
# Walk-in / flash-day / macho-biker FRAMING (the pack bans these). Targeted so a legit
# mention of "flash art" is not caught — only the banned framings are.
_FRAMING_RE = re.compile(r"\bwalk[- ]?in\b|\bflash\s+(day|friday|pricing|deal|sale)\b|\b(biker|macho)\b", re.I)


def _dsn(dsn: str | None = None) -> str:
    return dsn or os.environ.get("ENGINE_DATABASE_URL") or _DEFAULT_DSN


def _artist_slug(artist: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (artist or "").lower()).strip("-")


def _norm_key(term: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (term or "").lower()) or "default"


def _join(items: list[str]) -> str:
    items = [i for i in items if i]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


# --------------------------------------------------------------------------- #
# Voice resolution (structured — the composer needs lexicon/bans/policies, not the
# rendered markdown string).
# --------------------------------------------------------------------------- #
@dataclass
class VoiceBundle:
    tone: list[str] = field(default_factory=list)
    structure: list[str] = field(default_factory=list)
    prefer: list[str] = field(default_factory=list)
    ban: list[str] = field(default_factory=list)
    approved_claims: list[str] = field(default_factory=list)
    emoji_allowed: list[str] = field(default_factory=list)
    emoji_max: int = 0
    hashtag_min: int = 3
    hashtag_max: int = 6
    example_hashtags: list[str] = field(default_factory=list)
    resolved: bool = False
    # The tenant this voice was actually resolved for — so the grounding label records
    # the REAL tenant, never a hardcoded fixture id (CustomerAcq-wwy.7 r8).
    tenant_id: str | None = None


def _parse_emoji_policy(policy: str) -> tuple[list[str], int]:
    """(allowed emoji, max per caption). Allowed = emoji appearing BEFORE 'never'/'except'
    in the policy; max = the upper bound of the first 'N-M' (else the first integer, else
    0). Conservative: if nothing parses, ``([], 0)`` — zero emoji always satisfies a
    '0-N' policy, so we never over-emit."""
    if not policy:
        return [], 0
    head = re.split(r"\bnever\b|\bexcept\b", policy, maxsplit=1, flags=re.I)[0]
    allowed = _EMOJI_RE.findall(head)
    # de-dupe, drop any hype emoji defensively
    seen: set[str] = set()
    out = [e for e in allowed if e not in _HYPE_EMOJI and not (e in seen or seen.add(e))]
    m = re.search(r"(\d+)\s*[-–]\s*(\d+)", policy)
    if m:
        mx = int(m.group(2))
    else:
        m1 = re.search(r"(\d+)", policy)
        mx = int(m1.group(1)) if m1 else 0
    return out, mx


def _parse_hashtag_policy(policy: str) -> tuple[int, int, list[str]]:
    """(min, max, example tags without '#'). Defaults (3, 6, []) when unparseable."""
    if not policy:
        return 3, 6, []
    m = re.search(r"(\d+)\s*[-–]\s*(\d+)", policy)
    lo, hi = (int(m.group(1)), int(m.group(2))) if m else (3, 6)
    examples = [t.lower() for t in re.findall(r"#(\w+)", policy)]
    # Drop tags the policy names as spam (they appear after 'never').
    tail = re.split(r"\bnever\b", policy, maxsplit=1, flags=re.I)
    if len(tail) > 1:
        spam = {t.lower() for t in re.findall(r"#(\w+)", tail[1])}
        examples = [t for t in examples if t not in spam]
    return lo, hi, examples


def resolve_voice(tenant_id: str | None = None) -> VoiceBundle:
    """Structured brand-voice bundle for the SENDING tenant. Degrades to an unresolved
    empty bundle (``resolved=False``) if the pack can't load OR no tenant resolves — the
    caption then composes from artwork facts only, with no emoji and a plain CTA (never a
    fabricated voice, never a fixture stand-in).

    ``resolve_voice(None)`` resolves the fixture default ONLY via the same explicit dev
    flag as :func:`studio.customer_research.resolve_brand_voice` (CustomerAcq-wwy.7 r8):
    a real tenant that passes no id degrades honestly rather than borrowing ladies8391's
    voice."""
    from studio.customer_research import _default_tenant

    tid = tenant_id or _default_tenant()
    if not tid:
        return VoiceBundle()
    try:
        from config.loader import load_pack
        from kb.voice import load_voice_dimensions

        dims = load_voice_dimensions(load_pack(tid))
        v = dims.vocabulary
        emoji_allowed, emoji_max = _parse_emoji_policy(v.emoji_policy)
        h_lo, h_hi, h_ex = _parse_hashtag_policy(v.hashtag_policy)
        return VoiceBundle(
            tone=list(dims.tone),
            structure=list(dims.structure),
            prefer=list(v.prefer),
            ban=list(v.ban),
            approved_claims=list(v.approved_claims),
            emoji_allowed=emoji_allowed,
            emoji_max=emoji_max,
            hashtag_min=h_lo,
            hashtag_max=h_hi,
            example_hashtags=h_ex,
            resolved=True,
            tenant_id=tid,
        )
    except Exception:
        return VoiceBundle()


# --------------------------------------------------------------------------- #
# The no-fabrication + voice gate.
# --------------------------------------------------------------------------- #
def _concrete_bans(bans: list[str]) -> list[str]:
    """Short, concrete banned phrases we can substring-check without false positives.
    Meta-bans (e.g. 'AI-tells: …', 'price/discount language', 'superlatives') are
    enforced structurally by the regex tripwires + by construction instead."""
    out: list[str] = []
    for b in bans:
        b = (b or "").strip()
        if not b:
            continue
        # Skip descriptive/meta entries: they contain ':', '(', '/', or read as a rule.
        if any(ch in b for ch in (":", "(", "/")) or len(b.split()) > 5:
            continue
        if any(w in b.lower() for w in ("superlative", "framing", "claim", "language", "promise", "emoji")):
            continue
        out.append(b.lower())
    return out


def check_caption(text: str, voice: VoiceBundle) -> list[str]:
    """Return a list of gate violations for ``text`` (empty list == clean). Enforces:
    concrete brand bans, no hype emoji, no price/discount, no scarcity, no superlatives,
    no em-dash drama, and the pack's emoji cap. Pure — the composer calls it as a
    fail-closed tripwire and tests assert both a clean pass and a caught bad string."""
    low = text.lower()
    violations: list[str] = []
    for b in _concrete_bans(voice.ban):
        if b and b in low:
            violations.append(f"banned phrase: {b!r}")
    if any(e in text for e in _HYPE_EMOJI):
        violations.append("hype emoji present")
    if _DISCOUNT_RE.search(text):
        violations.append("price/discount language")
    if _SCARCITY_RE.search(text):
        violations.append("fabricated scarcity/urgency")
    if _SUPERLATIVE_RE.search(text):
        violations.append("superlative claim")
    if _FRAMING_RE.search(text):
        violations.append("walk-in/flash or macho framing")
    if "—" in text:  # em-dash — the pack bans em-dash-as-drama; avoid entirely
        violations.append("em-dash (AI-tell)")
    emoji_n = len(_EMOJI_RE.findall(text))
    cap = voice.emoji_max if voice.resolved else 0
    if emoji_n > cap:
        violations.append(f"too many emoji ({emoji_n} > {cap})")
    return violations


# --------------------------------------------------------------------------- #
# Caption composition — deterministic, voice-aware, platform-specific, grounded.
# --------------------------------------------------------------------------- #
@dataclass
class PostCaption:
    platform: str
    body: str
    hashtags: list[str]
    call_to_action: str
    grounding: list[str]

    def render(self) -> str:
        """The full post text staged as the action ``draft``. IG stacks the hashtags on
        their own line; FB keeps them inline-light (platform-appropriate)."""
        parts = [self.body.strip(), self.call_to_action.strip()]
        text = "\n\n".join(p for p in parts if p)
        if self.hashtags:
            tags = " ".join(f"#{h}" for h in self.hashtags)
            text = f"{text}\n\n{tags}" if self.platform == PLATFORM_INSTAGRAM else f"{text}\n{tags}"
        return text


def _lexicon(voice: VoiceBundle, *needles: str, default: str = "") -> str:
    """First preferred-lexicon phrase containing any needle (case-insensitive), else the
    default. Keeps the opener anchored in the pack's OWN approved words."""
    for phrase in voice.prefer:
        pl = phrase.lower()
        if any(n in pl for n in needles):
            return phrase
    return default


def _free_consult_claim(voice: VoiceBundle) -> bool:
    """True iff the pack lists a free-consultation approved claim (so the CTA may say so;
    otherwise the CTA stays a plain invite — never an unsubstantiated policy)."""
    return any("free" in c.lower() and "consult" in c.lower() for c in voice.approved_claims)


def _hashtags_for(
    pick: ArtworkPick | None,
    voice: VoiceBundle,
    *,
    limit: int,
    extra_tags: tuple[str, ...] = (),
) -> list[str]:
    """Grounded hashtags: the picked piece's REAL styles/motifs first, then any approved
    ``extra_tags`` (a collection/season tag for the theme angle — Rec 5), then the pack's
    approved example tags. Lowercased, de-duped, capped at ``limit``. Never spam tags;
    every tag traces to a real style/motif, an approved collection/season, or a pack
    example."""
    out: list[str] = []
    seen: set[str] = set()

    def _add(tag: str) -> None:
        t = re.sub(r"[^a-z0-9]", "", tag.lower())
        if t and t not in seen:
            seen.add(t)
            out.append(t)

    if pick:
        for s in pick.styles:
            _add(re.sub(r"[^a-z0-9]", "", s.lower()) + "tattoo")
        for mtf in pick.motifs:
            _add(re.sub(r"[^a-z0-9]", "", mtf.lower()) + "tattoo")
    for ex in extra_tags:
        _add(ex)
    for ex in voice.example_hashtags:
        _add(ex)
    return out[:limit]


# --------------------------------------------------------------------------- #
# Angle layer (Rec 1/2/5) — pure, deterministic angle selection. The angle swaps ONLY
# the opener hook + one framing sentence + the CTA (+ one approved theme hashtag). Every
# concrete token still comes from the pack lexicon or a real stored artwork field; no
# angle introduces a price, a count, urgency, a superlative, or banned framing — the
# outputs stay inside ``check_caption``.
# --------------------------------------------------------------------------- #
def _canon(term: str | None) -> str:
    """Canonical token: lowercase, non-alphanumerics dropped ('fine-line'->'fineline')."""
    return re.sub(r"[^a-z0-9]", "", (term or "").lower())


# Rec 1 — pure style -> angle. Keyed on the canonical style token.
_STYLE_ANGLE: dict[str, str] = {
    "fineline": "made_for_you",
    "micro": "made_for_you",
    "minimalist": "made_for_you",
    "floral": "made_for_you",
    "botanical": "made_for_you",
    "traditional": "artist_spotlight",
    "neotraditional": "artist_spotlight",
    "color": "artist_spotlight",
    "blackwork": "artist_spotlight",
    "geometric": "artist_spotlight",
    "script": "your_words",
    "lettering": "your_words",
    "coverup": "fresh_start",
}
_DEFAULT_ANGLE = "made_for_you"

# Rec 2 — theme/collection -> seasonal/availability angle. Keyed on canonical token.
_THEME_ANGLE: dict[str, str] = {
    "lunchmenu": "new_flash_sheet",
    "4thofjuly": "seasonal",
    "pride": "community",
    "buildapin": "build_your_own",
    "charm": "build_your_own",
}

# Rec 5 — one approved collection/season hashtag per theme angle (no location tag unless
# the pack supplies one; the pack has no location field today, so none is emitted).
_THEME_HASHTAG: dict[str, str] = {
    "pride": "pridetattoo",
    "4thofjuly": "patriotictattoo",
    "lunchmenu": "flashtattoo",
    "buildapin": "flashtattoo",
    "charm": "flashtattoo",
}

# Rec 5 — CTA per angle. IG is lowercase/DM-first; FB is a sentence. Both fail-closed
# clean (no fabricated urgency); the free-consult clause is appended only if approved.
_ANGLE_CTA_IG: dict[str, str] = {
    "made_for_you": "dm to start your design.",
    "artist_spotlight": "dm to book this piece.",
    "your_words": "dm to start your lettering.",
    "fresh_start": "dm to start your cover-up.",
    "new_flash_sheet": "dm to book yours.",
    "seasonal": "dm to book yours.",
    "community": "dm to book yours.",
    "build_your_own": "pick your charms, dm to build your piece.",
}
_ANGLE_CTA_FB: dict[str, str] = {
    "made_for_you": "Send me a message to start your design.",
    "artist_spotlight": "Send me a message to book this piece.",
    "your_words": "Send me a message to start your lettering.",
    "fresh_start": "Send me a message to start your cover-up.",
    "new_flash_sheet": "Send me a message to book yours.",
    "seasonal": "Send me a message to book yours.",
    "community": "Send me a message to book yours.",
    "build_your_own": "Pick your charms and send me a message to build your piece.",
}


def pick_angle(pick: ArtworkPick | None, theme: str | None = None) -> str:
    """Rec 1: choose a STYLE angle from the pick's matched (else own) styles, falling back
    to the theme/collection token, then the default ``made_for_you``. Pure + deterministic
    (needed for exactly-once staging)."""
    styles: list[str] = []
    if pick is not None:
        styles = list(pick.matched_styles or pick.styles or [])
    for s in styles:
        a = _STYLE_ANGLE.get(_canon(s))
        if a:
            return a
    coll = getattr(pick, "collection", "") if pick is not None else ""
    for t in (theme, coll):
        a = _STYLE_ANGLE.get(_canon(t))
        if a:
            return a
    return _DEFAULT_ANGLE


def theme_angle(theme: str | None = None, collection: str | None = None) -> str | None:
    """Rec 2: the seasonal/availability angle named by the piece's ``collection`` (preferred,
    so the angle + hashtag always match the actual picked piece) or the post ``theme``.
    ``None`` when neither names a known flash concept. Pure + deterministic."""
    for t in (collection, theme):
        a = _THEME_ANGLE.get(_canon(t))
        if a:
            return a
    return None


def _theme_hashtag(theme: str | None, collection: str | None) -> str:
    """The one approved collection/season hashtag for the active theme angle (from the
    piece's collection first, then the theme). ``''`` when none applies."""
    for t in (collection, theme):
        h = _THEME_HASHTAG.get(_canon(t))
        if h:
            return h
    return ""


def _sentence(frag: str) -> str:
    """Capitalize the first character of a lowercase clause (for the FB register)."""
    frag = frag.strip()
    return frag[:1].upper() + frag[1:] if frag else frag


def _angle_lines(angle: str, *, os_: str, om: str, piece_l: str, st: str) -> list[str]:
    """The angle's opener hook + framing/piece clauses, all LOWERCASE and grounded:
    ``os_``/``om`` are pack-lexicon phrases (your story / made for you); ``piece_l`` is the
    real artwork caption; ``st`` is the real style tag. Theme angles fold the piece into an
    availability sentence using only 'new flash sheet'/'up to book'/'available to book'
    (never 'flash day/friday/sale/pricing'); scarcity stays honest (exists + bookable)."""
    if angle in ("new_flash_sheet", "community"):
        return ["new flash sheet.", f"{piece_l}, up to book."]
    if angle == "seasonal":
        return ["new flash sheet.", f"{piece_l}, available to book."]
    if angle == "build_your_own":
        return ["build your own piece.", f"{piece_l}, up to book."]
    if angle == "artist_spotlight":
        if st:
            return [f"{st}.", f"{piece_l}.", f"{om}."]
        return [f"{om}.", f"{piece_l}."]
    if angle == "your_words":
        opener = f"{os_}, in {st}." if st else f"{os_}, {om}."
        return [opener, f"{piece_l}.", f"{om}."]
    if angle == "fresh_start":
        base = [f"a fresh start, {om}.", f"{piece_l}."]
        if st:
            base.append(f"{st}, {om}.")
        return base
    # made_for_you (default)
    base = [f"{os_}, {om}.", f"{piece_l}."]
    if st:
        base.append(f"{st}, {om}.")
    return base


def compose_caption(
    *,
    platform: str,
    artist: str,
    pick: ArtworkPick | None,
    voice: VoiceBundle,
    theme: str | None = None,
) -> PostCaption:
    """Compose ONE platform caption. Deterministic + grounded: every concrete token
    traces to the artist name, the picked artwork's real caption/tags, the pack's
    approved lexicon, or an approved claim. IG and FB differ in register, emoji, CTA and
    hashtag weight so the two drafts are genuinely distinct, not duplicated."""
    grounding: list[str] = [f"artist={artist}"]
    is_ig = platform == PLATFORM_INSTAGRAM

    opener_story = _lexicon(voice, "your story", "story", default="your story")
    opener_made = _lexicon(voice, "made for you", "drawn for you", default="made for you")
    care = _lexicon(voice, "no rush", "take our time", default="")
    space = _lexicon(voice, "safe space", "women-first", default="")
    if voice.resolved:
        # Record the REAL tenant the voice resolved for — never a hardcoded fixture id
        # (CustomerAcq-wwy.7 r8: a skindesign post must not log brand_voice=ladies8391).
        grounding.append(
            f"brand_voice={voice.tenant_id}" if voice.prefer else "brand_voice=empty"
        )

    # Emoji: at most one allowed glyph on IG, none on FB (lighter register). Only ever a
    # pack-allowed emoji; zero if the pack didn't sanction any.
    emoji = ""
    if is_ig and voice.emoji_allowed and voice.emoji_max >= 1:
        emoji = " " + voice.emoji_allowed[0]
        grounding.append(f"emoji={voice.emoji_allowed[0]}")

    # Angle selection (Rec 1/2): a theme/seasonal angle (named by the piece's collection
    # or the post theme) takes precedence for the availability hook; otherwise the style
    # angle drives. Fully deterministic — needed for exactly-once staging.
    collection = getattr(pick, "collection", "") if pick is not None else ""
    t_ang = theme_angle(theme, collection)
    angle = t_ang or pick_angle(pick, theme)
    grounding.append(f"angle={angle}")

    lines: list[str] = []
    if pick and pick.caption:
        grounding.append(f"artwork_asset={pick.asset_id}")
        grounding.append(f"artwork_caption={pick.caption}")
        piece = pick.caption.rstrip(".")
        style_tag = _join(pick.matched_styles or pick.styles)
        if style_tag:
            grounding.append(f"artwork_styles={style_tag}")
        if collection:
            grounding.append(f"artwork_collection={collection}")
        if t_ang:
            # Honest availability: the piece exists and is up to book — never a count.
            grounding.append("availability=exists-bookable")
        frags = _angle_lines(
            angle,
            os_=opener_story,
            om=opener_made.lower(),
            piece_l=piece.lower(),
            st=style_tag.lower(),
        )
        if is_ig:
            lines.append(frags[0] + emoji)
            lines.extend(frags[1:])
        else:
            lines.append(" ".join(_sentence(f) for f in frags))
    else:
        # HONEST no-artwork path: we say we'd attach the right piece on approval; we do
        # not invent one.
        grounding.append("artwork=none-on-file")
        if is_ig:
            lines.append(f"{opener_story}, {opener_made.lower()}.{emoji}")
            lines.append(f"a custom {artist} piece, drawn for you.")
        else:
            lines.append(
                f"{opener_story.capitalize()}, {opener_made.lower()}. "
                f"A custom {artist} piece, drawn for you."
            )

    tail_bits = [b for b in (care, space) if b]
    if tail_bits:
        tail = _join([b.lower() for b in tail_bits])
        grounding.append("voice_values=" + ",".join(tail_bits))
        # A short brand creed in the pack's OWN phrases (fragments are idiomatic here);
        # IG lowercase, FB sentence-case. No forced conjugation that could read awkwardly.
        creed = f"{tail}."
        lines.append(creed if is_ig else creed[:1].upper() + creed[1:])

    body = "\n".join(lines) if is_ig else " ".join(lines)

    # CTA — an angle-aware warm invite (Rec 5); free-consult only if it is an approved
    # claim. Every CTA is pre-cleared against check_caption (no fabricated urgency).
    consult = _free_consult_claim(voice)
    if consult:
        grounding.append("claim=free-consultation")
    grounding.append(f"cta_angle={angle}")
    if is_ig:
        cta = _ANGLE_CTA_IG.get(angle, _ANGLE_CTA_IG[_DEFAULT_ANGLE])
        cta = cta + (" consults are free." if consult else "")
    else:
        cta = _ANGLE_CTA_FB.get(angle, _ANGLE_CTA_FB[_DEFAULT_ANGLE])
        cta = cta + (" Consults are always free." if consult else "")

    # Rec 5: add one approved collection/season hashtag for the theme angle (from the
    # piece's collection first). No location tag — the pack supplies no location field.
    theme_tag = _theme_hashtag(theme, collection) if t_ang else ""
    extra_tags = (theme_tag,) if theme_tag else ()
    if theme_tag:
        grounding.append(f"theme_hashtag={theme_tag}")
    limit = voice.hashtag_max if is_ig else min(2, voice.hashtag_max)
    hashtags = _hashtags_for(pick, voice, limit=limit, extra_tags=extra_tags)
    # IG honours the pack minimum; FB stays deliberately light.
    if is_ig and len(hashtags) < voice.hashtag_min:
        grounding.append(f"hashtags=below-min({len(hashtags)})")
    if hashtags:
        grounding.append("hashtags=" + ",".join(hashtags))

    return PostCaption(
        platform=platform,
        body=body,
        hashtags=hashtags,
        call_to_action=cta,
        grounding=grounding,
    )


def voice_post_fields(
    tenant_id: str,
    *,
    styles: list[str] | None = None,
    motifs: list[str] | None = None,
    theme: str | None = None,
) -> dict[str, Any]:
    """Grounded IG post fields (hashtags + CTA + angle) for REAL tags — the same
    pack-policy helpers the standalone drafter uses, exposed for the supervisor IG
    pipeline (engine-core item 6c) to land on staged post actions' context.

    Every hashtag traces to a provided real style/motif or a pack example; the CTA
    is the deterministic angle CTA (+ the free-consult clause only when the pack
    approves that claim). Honest-empty tags yield pack-example hashtags only."""
    voice = resolve_voice(tenant_id)
    pick = None
    if styles or motifs:
        pick = ArtworkPick(
            asset_id="", artist="", image_ref="", caption="",
            matched_styles=[], matched_motifs=[], score=0, exact_match=False,
            why="", styles=list(styles or []), motifs=list(motifs or []),
        )
    angle = theme_angle(theme) or pick_angle(pick, theme)
    cta = _ANGLE_CTA_IG.get(angle, _ANGLE_CTA_IG[_DEFAULT_ANGLE])
    if _free_consult_claim(voice):
        cta = cta + " consults are free."
    limit = voice.hashtag_max if voice.resolved else 6
    theme_tag = _theme_hashtag(theme, None)
    hashtags = _hashtags_for(
        pick, voice, limit=limit, extra_tags=(theme_tag,) if theme_tag else ()
    )
    return {"hashtags": hashtags, "cta": cta, "angle": angle}


# --------------------------------------------------------------------------- #
# Orchestration — pick artwork, compose per platform, stage HELD (exactly-once).
# --------------------------------------------------------------------------- #
def _context_blob(pick: ArtworkPick | None) -> str:
    """Readable review-queue context linking the draft to its artwork (or honest none)."""
    if pick is None:
        return "No artwork on file for this artist yet; a piece is attached on approval."
    return f"Artwork: {pick.image_ref} (asset {pick.asset_id}). Why: {pick.why}"


def _context_payload(pick: ArtworkPick | None, broll_asset_id: str | None) -> str:
    """The action ``context`` JSON: the readable note (:func:`_context_blob`,
    preserved under ``note`` — the same key :func:`studio.ig_pipeline.
    enrich_post_actions` folds legacy text into) plus the machine-readable media
    references the social ready queue resolves: ``artwork_asset_id`` (the picked
    piece's REAL ``assets`` row id) and the optional ``broll_asset_id`` (the
    newest real video on file). Ids are never invented — no pick, no key."""
    payload: dict[str, Any] = {"note": _context_blob(pick)}
    if pick is not None:
        payload["artwork_asset_id"] = pick.asset_id
        payload["artwork"] = {
            "assetId": pick.asset_id,
            "styles": list(pick.styles),
            "motifs": list(pick.motifs),
        }
    if broll_asset_id:
        payload["broll_asset_id"] = broll_asset_id
    return json.dumps(payload, ensure_ascii=False)


def draft_studio_posts(
    *,
    tenant_id: str = _DEFAULT_TENANT,
    artist_name: str,
    theme: str | None = None,
    platforms: tuple[str, ...] = DEFAULT_PLATFORMS,
    dsn: str | None = None,
) -> dict[str, Any]:
    """Draft + STAGE HELD IG/FB posts for one artist. Returns a structured result the
    preview panel + the proof consume.

    Staging is exactly-once: the idempotency_key is deterministic per (tenant, artist,
    theme, platform), so re-running returns the SAME action ids. Nothing is published —
    every row is a PENDING ``actions`` row on the existing approve-first path."""
    from actions.store import ensure_schema, record_pending_action

    tenant_id = tenant_id or _DEFAULT_TENANT
    slug = _artist_slug(artist_name)
    theme_key = _norm_key(theme)
    resolved_dsn = _dsn(dsn)

    ensure_schema(resolved_dsn)

    artworks = list_artwork(tenant_id, artist_name, dsn=resolved_dsn)
    styles = _artist_styles(artworks)
    theme_terms = [theme] if theme else styles
    pick = select_artwork(artworks, artist_styles=styles, theme_terms=theme_terms)

    voice = resolve_voice(tenant_id)
    run_id = f"studio-post:{tenant_id}:{slug}:{theme_key}"

    # Optional b-roll reference for the ready queue: the newest REAL video asset
    # on file for this artist (honest-empty [] when none / store unavailable).
    from studio.ig_pipeline import load_broll

    broll = load_broll(tenant_id, artist_name, dsn=resolved_dsn)
    broll_asset_id = broll[0]["asset_id"] if broll else None

    drafts: list[dict[str, Any]] = []
    for platform in platforms:
        cap = compose_caption(
            platform=platform, artist=artist_name, pick=pick, voice=voice, theme=theme
        )
        draft_text = cap.render()
        violations = check_caption(draft_text, voice)
        if violations:
            # Fail-closed: never stage a caption that trips a fabrication/voice gate.
            raise ValueError(
                f"caption gate failed for {platform}: {violations}\n---\n{draft_text}"
            )
        idem = f"studio-post:{tenant_id}:{slug}:{theme_key}:{platform}"
        action_id = record_pending_action(
            tenant_id=tenant_id,
            decision_id=None,
            type="post",
            channel=platform,
            worker="studio_post_campaign",
            target=None,
            draft=draft_text,
            context=_context_payload(pick, broll_asset_id),
            conf=None,
            threshold=None,
            esc_kind="approval_required",
            esc_label="Studio post — operator approval required",
            idempotency_key=idem,
            run_id=run_id,
            dsn=resolved_dsn,
        )
        drafts.append({
            "platform": platform,
            "action_id": action_id,
            "idempotency_key": idem,
            "held": True,
            "caption": cap.body,
            "hashtags": cap.hashtags,
            "call_to_action": cap.call_to_action,
            "draft": draft_text,
            "artwork": pick.to_dict() if pick else None,
            "grounding": cap.grounding,
        })

    return {
        "tenant_id": tenant_id,
        "artist": artist_name,
        "theme": theme,
        "run_id": run_id,
        "has_artwork": pick is not None,
        "artwork": pick.to_dict() if pick else None,
        "voice_resolved": voice.resolved,
        "drafts": drafts,
    }


if __name__ == "__main__":  # pragma: no cover
    import argparse
    import io
    import json
    import sys

    from studio.artwork_select import seed_studio_artwork

    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

    ap = argparse.ArgumentParser(description="Draft HELD IG/FB studio posts for an artist.")
    ap.add_argument("--tenant", default=os.environ.get("STUDIO_TENANT_ID", _DEFAULT_TENANT))
    ap.add_argument("--artist", default="Maya")
    ap.add_argument("--theme", default=None)
    ap.add_argument("--seed", action="store_true", help="Seed the mock portfolio first.")
    args = ap.parse_args()

    if args.seed:
        seeded = seed_studio_artwork(args.tenant)
        print(f"[seed] {len(seeded)} portfolio assets ensured for {args.tenant}")

    result = draft_studio_posts(
        tenant_id=args.tenant, artist_name=args.artist, theme=args.theme
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
