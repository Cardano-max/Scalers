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

from cells.ai_flagger import FlaggerConfig, ai_flagger
from cells.base import Cell
from cells.post_schemas import MediaKind, PostDraft, Platform
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

# Pinned drafting tier (stack-decision.md: Opus 4.8 for the hardest writing).
# defer_model_check in Cell means the id is validated at run, not construction.
DRAFTING_MODEL = "anthropic:claude-opus-4-8"

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
    *, grounding: VoiceGrounding, platform: Platform, config: FlaggerConfig = FlaggerConfig()
) -> ValidatorBank:
    """The in-loop deterministic gates a PostDraft must clear (ADR Decision 4)."""
    vocab = grounding.dimensions.vocabulary
    return ValidatorBank(
        validators=(
            non_empty("caption"),
            non_empty("call_to_action"),
            no_placeholder("caption"),
            no_placeholder("call_to_action"),
            caption_length(platform),
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
    model: str = DRAFTING_MODEL,
    **overrides,
) -> Cell[PostDraft]:
    """Build the Draft (Create) cell — pinned drafting model, temp 0, in-loop bank."""
    params = dict(
        name="draft",
        schema=PostDraft,
        instructions=build_draft_instructions(grounding, platform),
        validators=draft_validators(grounding=grounding, platform=platform, config=config),
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
