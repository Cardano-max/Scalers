"""Draft (Create) cell — brand-voice-grounded PostDraft (a9m.5 / POST-01b).

Turns the selected **Angle** (a9m.4) + the typed **VoiceGrounding** (a9m.3) into a
validated :class:`~cells.post_schemas.PostDraft` (caption, hashtags, media spec) —
no raw model text downstream. It is the copywriter skill (1mk.5) applied to the
posting artifact, grounded by brand-voice (a9m.3 / 1mk.2) and gated by the
deterministic **AI-flagger** (AF-01..08, 1mk.3) plus the per-tenant ban/claim
lists carried on the grounding.

The LLM authenticity pass is a **separate** cell (ADR: a writer must not grade its
own authenticity, AUTON-04). This cell's bank is the *deterministic* gate; the
harness Check&Score node (a9m.7) + 439-held router decide auto/review/regenerate —
this cell never publishes. Persistent off-voice/banned output → ``CellError`` on a
code path (never emitted).

Composition order in the prompt is the contract: **grounding before task**, so the
model reads the artist's voice before it writes.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from cells.ai_flagger import FlaggerConfig, ai_flagger
from cells.base import Cell
from cells.offer_guard import SubstantiatedOffer, no_unsubstantiated_offers
from cells.content_brief import Platform
from cells.post_schemas import MediaKind, PostDraft
from cells.validators import (
    FieldValidator,
    Severity,
    ValidationIssue,
    ValidatorBank,
    no_placeholder,
    non_empty,
)
from harness.state import GraphState
from kb.voice import VoiceGrounding

# Pinned drafting tier — POLICY-CLAMPED to haiku-4.5 (CustomerAcq-8sk, operator
# order 2026-07-02; was opus-4-8 per stack-decision — restore only when the
# operator lifts the policy). defer_model_check in Cell means the id is
# validated at run, not construction.
DRAFTING_MODEL = "anthropic:claude-haiku-4-5"

# Per-platform hard caption caps (chars).
_CAPTION_MAX = {Platform.INSTAGRAM: 2200, Platform.FACEBOOK: 5000}
_HASHTAG_MIN, _HASHTAG_MAX, _HASHTAG_HARD = 3, 6, 10
# Credential / numeric / offer claim shapes (superlatives + pain live in `ban`).
_CLAIM_RE = re.compile(
    r"\b(\d+\s*\+?\s*(?:years?|yrs)\b|\d+\s*%|guarantee\w*|certified|licen[sc]ed|"
    r"award\w*|voted|free)\b",
    re.IGNORECASE,
)
_TOKEN_RE = re.compile(r"[a-z0-9']+")
_STOP = frozenset(
    "the a an and or to of for in on with you your i we it is are be this that".split()
)


def _txt(value, field: str) -> str:
    return getattr(value, field, "") or ""


# ── Deterministic validators (run IN the cell repair loop) ───────────────────


def caption_length(platform: Platform) -> FieldValidator:
    cap = _CAPTION_MAX.get(platform, 2200)

    def _fn(value):
        n = len(_txt(value, "caption"))
        if n > cap:
            return [
                ValidationIssue(
                    "caption_length",
                    Severity.ERROR,
                    f"caption {n} chars > {platform.value} limit {cap}",
                )
            ]
        return []

    return FieldValidator("caption_length", _fn)


#: Openers and phrases that belong in an EMAIL, never in a public social caption.
#: A caption is read by a stranger scrolling a feed — not by a person who was written to.
#: The channel-style prompt already SAYS "never open with an email-style greeting" and the
#: model still shipped `Hey! Keebs has been diving deep into botanical pieces lately.` on a
#: live Instagram post: a prompt is advice, and advice loses. This is the gate.
_EMAIL_OPENER_RE = re.compile(
    r"""^\s*(
        (hey|hi|hello|greetings|dear)\b            # Hey! / Hi there / Hey Amanda / Dear …
      | (good\s+(morning|afternoon|evening))\b
    )""",
    re.IGNORECASE | re.VERBOSE,
)
#: THE MESSENGER VOICE — the deepest version of this bug, and the one a phrase-list misses.
#: A caption is the STUDIO speaking. It is not a person relaying a message on the artist's
#: behalf. Ban the phrase "wanted me to reach out" and the model simply writes "Keebs wanted
#: me to share" — same voice, different words, and it shipped. So the PATTERN is banned:
#: any "<someone> wanted/asked me to <verb>" construction, whoever is named.
_MESSENGER_RE = re.compile(
    r"\b(wanted|asked|told)\s+me\s+to\b|\bon\s+behalf\s+of\b|\bwanted\s+(you\s+)?to\s+(know|share|tell)\b",
    re.IGNORECASE,
)
#: Message-shaped phrases anywhere in a caption — the register of a 1:1 note, not a post.
_EMAIL_REGISTER = (
    "reaching out",
    "reach out to you",
    "just checking in",
    "checking in with you",
    "wanted to follow up",
    "following up",
    "hope you're well",
    "hope you are well",
    "hope this finds you",
    "let me know if you",
    "thought of you",
    "as promised",
)


def social_post_voice(platform: Platform) -> FieldValidator:
    """HARD: a public POST must not be written like a message.

    An Instagram caption and a Facebook page post are read by strangers in a feed. They
    have no salutation, no addressee and no sign-off — the first line is a hook that earns
    the scroll-stop, not a greeting. A real run shipped an IG caption opening
    ``"Hey! Keebs has been diving deep into botanical pieces lately."`` while the very same
    prompt instructed the model never to do that: a prompt is advice, and the model is free
    to ignore it. This validator is not advice — an ERROR here fails the draft and the cell
    regenerates, so an email-shaped caption cannot reach the operator.

    Only applies to POSTING platforms; email/SMS drafting is untouched."""
    if platform.value not in ("instagram", "facebook", "ig", "fb", "reels", "tiktok"):
        return FieldValidator("social_post_voice", lambda _v: [])

    def _fn(value):
        caption = _txt(value, "caption")
        issues: list[ValidationIssue] = []
        first_line = next((ln for ln in caption.splitlines() if ln.strip()), "")
        if _EMAIL_OPENER_RE.match(first_line):
            issues.append(
                ValidationIssue(
                    "social_post_voice",
                    Severity.ERROR,
                    f"a {platform.value} caption is a PUBLIC post, not a message: it opens "
                    f"with an email-style greeting ({first_line[:40]!r}). Open with a hook "
                    "about what is IN the picture — no salutation, no addressee.",
                )
            )
        # The MESSENGER voice, anywhere in the caption. A caption is the studio speaking —
        # not someone relaying a message on the artist's behalf ("Keebs wanted me to share…").
        m = _MESSENGER_RE.search(caption)
        if m:
            issues.append(
                ValidationIssue(
                    "social_post_voice",
                    Severity.ERROR,
                    f"{m.group(0)!r} — the caption is written as if RELAYING a message on "
                    "the artist's behalf. A post is the studio speaking in its own voice, "
                    "not an intermediary passing something along.",
                )
            )
        low = caption.lower()
        for phrase in _EMAIL_REGISTER:
            if phrase in low:
                issues.append(
                    ValidationIssue(
                        "social_post_voice",
                        Severity.ERROR,
                        f"{phrase!r} is 1:1 message register, not caption register — a "
                        "caption speaks to a feed, not to one person who was written to.",
                    )
                )
        return issues

    return FieldValidator("social_post_voice", _fn)


def ban_lexicon(ban: tuple[str, ...]) -> FieldValidator:
    """HARD: any per-tenant banned phrase (VoiceDimensions.vocabulary.ban) in the
    caption / CTA / hashtags blocks the draft (ADR Decision 4 banned_phrase gate)."""
    lowered = tuple(b.lower() for b in ban if b)

    def _fn(value):
        blob = " ".join(
            [
                _txt(value, "caption"),
                _txt(value, "call_to_action"),
                " ".join(getattr(value, "hashtags", []) or []),
            ]
        ).lower()
        return [
            ValidationIssue("ban_lexicon", Severity.ERROR, f"banned phrase {b!r}")
            for b in lowered
            if b in blob
        ]

    return FieldValidator("ban_lexicon", _fn)


def claim_discipline(approved_claims: tuple[str, ...]) -> FieldValidator:
    """HARD: a credential/offer claim not represented in ``approved_claims`` fails
    (ADR Decision 4 claim gate → regenerate-then-escalate). Conservative: only flags
    claim-shaped spans whose text is absent from the allow-list blob."""
    blob = " ".join(approved_claims).lower()

    def _fn(value):
        issues = []
        for field in ("caption", "call_to_action"):
            for m in _CLAIM_RE.finditer(_txt(value, field)):
                tok = m.group(0).lower().strip()
                if tok not in blob:
                    issues.append(
                        ValidationIssue(
                            "claim_discipline",
                            Severity.ERROR,
                            f"{field!r}: claim {tok!r} not in approved_claims",
                        )
                    )
        return issues

    return FieldValidator("claim_discipline", _fn)


def hashtags_policy(ban: tuple[str, ...]) -> FieldValidator:
    banned = {b.lower().lstrip("#") for b in ban}

    def _fn(value):
        tags = getattr(value, "hashtags", []) or []
        if not tags:
            return []
        issues = []
        if len(tags) > _HASHTAG_HARD:
            issues.append(
                ValidationIssue(
                    "hashtags_policy",
                    Severity.ERROR,
                    f"{len(tags)} hashtags > {_HASHTAG_HARD} (spam wall)",
                )
            )
        elif not (_HASHTAG_MIN <= len(tags) <= _HASHTAG_MAX):
            issues.append(
                ValidationIssue(
                    "hashtags_policy",
                    Severity.WARN,
                    f"{len(tags)} hashtags; {_HASHTAG_MIN}-{_HASHTAG_MAX} performs best",
                )
            )
        for t in tags:
            if t.startswith("#") or t != t.lower() or not re.fullmatch(r"[a-z0-9_]+", t or ""):
                issues.append(
                    ValidationIssue(
                        "hashtags_policy",
                        Severity.ERROR,
                        f"hashtag {t!r} must be lowercase, no '#', alnum/underscore",
                    )
                )
            if t.lower().lstrip("#") in banned:
                issues.append(
                    ValidationIssue("hashtags_policy", Severity.ERROR, f"banned hashtag {t!r}")
                )
        return issues

    return FieldValidator("hashtags_policy", _fn)


def _tokens(text: str) -> set[str]:
    return {t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOP and len(t) > 2}


def voice_similarity(exemplars, *, floor: float = 0.06) -> FieldValidator:
    """WARN if the caption shares little vocabulary with the on-voice exemplars
    (lexical proxy; the real cosine check is the eval's job). Skipped when SPARSE."""
    ex_tokens = [_tokens(e.content) for e in (exemplars or []) if getattr(e, "content", "")]

    def _fn(value):
        if not ex_tokens:
            return []
        cap = _tokens(_txt(value, "caption"))
        if not cap:
            return []
        best = (
            max((len(cap & et) / len(cap | et)) for et in ex_tokens if et)
            if any(ex_tokens)
            else 0.0
        )
        if best < floor:
            return [
                ValidationIssue(
                    "voice_similarity",
                    Severity.WARN,
                    f"caption shares little vocabulary with on-voice exemplars ({best:.2f})",
                )
            ]
        return []

    return FieldValidator("voice_similarity", _fn)


def media_valid() -> FieldValidator:
    """Per-kind media coherence (a9m.6 owns the full gate; this keeps the spec sane)."""

    def _fn(value):
        m = getattr(value, "media", None)
        if m is None:
            return [ValidationIssue("media_valid", Severity.ERROR, "media spec missing")]
        k, ar, dur = m.kind, m.aspect_ratio, m.duration_s
        bad = []
        if k is MediaKind.REEL:
            if ar != "9:16":
                bad.append("reel aspect_ratio must be 9:16")
            if not (isinstance(dur, (int, float)) and 5 <= dur <= 90):
                bad.append("reel duration_s must be 5-90")
        elif k in (MediaKind.IMAGE, MediaKind.CAROUSEL):
            if not ar:
                bad.append(f"{k.value} needs an aspect_ratio")
            if dur is not None:
                bad.append(f"{k.value} has no duration")
        elif k is MediaKind.TEXT:
            if ar is not None or dur is not None:
                bad.append("text post carries no media spec")
        return [ValidationIssue("media_valid", Severity.ERROR, b) for b in bad]

    return FieldValidator("media_valid", _fn)


def draft_validators(
    *,
    grounding: VoiceGrounding,
    platform: Platform,
    config: FlaggerConfig = FlaggerConfig(),
    offers: Sequence[SubstantiatedOffer] = (),
) -> ValidatorBank:
    """The in-loop deterministic gates a PostDraft must clear (ADR Decision 4).

    ``offers`` are the tenant's SUBSTANTIATED offers (65w.14 anti-fabrication):
    the default () is FAIL-CLOSED — with no operator-authorized offers doc, any
    discount code / percent-off / promo token in the draft blocks it. Seed/mock
    offers docs never substantiate (offer_guard.is_real_offer_source)."""
    vocab = grounding.dimensions.vocabulary
    return ValidatorBank(
        validators=(
            non_empty("caption"),
            non_empty("call_to_action"),
            no_unsubstantiated_offers("caption", offers),      # 65w.14 anti-fab
            no_unsubstantiated_offers("call_to_action", offers),
            no_placeholder("caption"),
            no_placeholder("call_to_action"),
            caption_length(platform),
            social_post_voice(platform),  # a POST is not a message — hard, not advisory
            ai_flagger("caption", config),  # AF-01..08 (incl. BANNED_SLOP)
            ai_flagger("call_to_action", config),
            ban_lexicon(tuple(vocab.ban)),  # per-tenant hard bans
            claim_discipline(tuple(vocab.approved_claims)),
            hashtags_policy(tuple(vocab.ban)),
            voice_similarity(grounding.exemplars),
            media_valid(),
        )
    )


# ── Instructions (grounding before task) ─────────────────────────────────────


def _bullets(items) -> str:
    return "\n".join(f"- {x}" for x in items) if items else "- (none on file)"


def build_draft_instructions(grounding: VoiceGrounding, platform: Platform) -> str:
    d = grounding.dimensions
    v = d.vocabulary
    parts = [
        "# BRAND VOICE — write as THIS artist, not generic AI. Source of truth.",
        "## Tone:",
        _bullets(d.tone),
        "## Structure:",
        _bullets(d.structure),
        f"## Emoji policy: {v.emoji_policy or '(per brand)'}",
        f"## Hashtag policy: {v.hashtag_policy or '(per brand)'}",
        "## Preferred lexicon:",
        _bullets(v.prefer),
        "## NEVER use (hard ban — beats everything):",
        _bullets(v.ban),
    ]
    shots = [e.content for e in grounding.exemplars[:4]]
    if shots:
        parts += ["## On-voice examples (mirror the rhythm; NEVER copy):", _bullets(shots)]
    elif grounding.low_grounding:
        parts += [
            "## NOTE: sparse grounding — write from the dimensions only; do not "
            "invent a voice. (Confidence will be lowered → human review.)"
        ]
    parts += [
        "",
        "# APPROVED CLAIMS — the ONLY factual/credential/offer claims allowed.",
        "A needed claim that is missing here is a BLOCKER (escalate), not a gap.",
        _bullets(v.approved_claims),
        "",
        "# TASK",
        "Turn the WINNING ANGLE (provided in the message) into ONE complete post draft "
        f"for {platform.value}: a caption, hashtags, a call-to-action, and a media spec.",
        "Rules: BRAND VOICE WINS over any pattern that fights it; only approved claims; "
        "no AI tells (em-dash, contrast framing, rule-of-three, hedging, generic "
        "transitions, listicle/emoji-bullet); no placeholders; lead with the client/"
        "story or the work. media: reel ⇒ aspect_ratio '9:16' + duration_s 5-90; "
        "image/carousel ⇒ aspect_ratio; text ⇒ no media. Always write a one-line brief.",
    ]
    return "\n".join(parts)


def render_angle_prompt(
    *, hook: str, rationale: str, format_hint: MediaKind | str | None = None
) -> str:
    """Render the selected Angle (a9m.4) into the per-run prompt for the cell."""
    fh = format_hint.value if isinstance(format_hint, MediaKind) else (format_hint or "")
    return (
        f"WINNING ANGLE\nhook: {hook}\nrationale: {rationale}\n"
        f"format_hint: {fh or '(choose the best media kind)'}"
    )


def build_draft_cell(
    *,
    grounding: VoiceGrounding,
    platform: Platform,
    config: FlaggerConfig = FlaggerConfig(),
    offers: Sequence[SubstantiatedOffer] = (),
    model: str = DRAFTING_MODEL,
    **overrides,
) -> Cell[PostDraft]:
    """Build the Draft (Create) cell — pinned drafting model, temp 0, in-loop bank.

    ``offers`` = the tenant's substantiated real offers (65w.14); default () is
    fail-closed (no offers doc -> no offer language allowed in the draft)."""
    params = dict(
        name="draft",
        schema=PostDraft,
        instructions=build_draft_instructions(grounding, platform),
        validators=draft_validators(
            grounding=grounding, platform=platform, config=config, offers=offers
        ),
        model=model,
    )
    params.update(overrides)
    return Cell(**params)


# ── Persistence — PostDraft lands in GraphState (ADR; AC) ─────────────────────


def persist_draft(state: GraphState, draft: PostDraft) -> GraphState:
    """Write the validated PostDraft into GraphState (artifact channel) + step_log."""
    return state.model_copy(
        update={
            "draft": draft,
            "step_log": [
                *state.step_log,
                f"draft: {draft.media.kind.value} post for {draft.platform.value}",
            ],
        }
    )
