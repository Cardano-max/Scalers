"""Copywriter cell — hook/CTA drafting from winning patterns (skill: copywriter,
CustomerAcq-1mk.5, Tier-3).

The copywriter takes a **scored winning angle** (the proven pattern/insight that
research + the strategist surfaced) and the **artist brand voice** (S2,
brand-voice) and molds the pattern into the artist's voice as several distinct
hook + caption + CTA drafts. Its output is gated by the deterministic
**AI-flagger** (S3, human-tone) wired into its validator bank, so a draft that
reads as AI slop is repaired or fails on a code path — then the harness jury +
confidence gate route it (never auto-ship without clearing them).

Composition (conditionally loaded by the harness on the copywriter cell):

* **S2 brand-voice** — the brand-voice context (positioning, pillars, approved
  claims, do/do-not, on-voice examples) is assembled by the brand-voice resolver
  and injected into this cell's instructions. The brand voice WINS over any
  pattern that fights it.
* **S3 AI-flagger** — :func:`~cells.ai_flagger.detect_ai_tells` runs over every
  variant's text as an ERROR validator (no em-dash / contrast framing / rule-of-
  three / generic transitions).

Edge cases (AC): a pattern that fights the brand voice → brand voice wins;
over-templated output → distinctness validator + variety guidance; platform length
limits → per-platform caps.

Re-authored from content-repurposing / "cook-the-blog" hook+CTA patterns
(retargeted to tattoo captions); see engine/skills/copywriter/. Registration for
agent use is gated on 1mk.1 sec-vetting + the eval gold-set (rvy.7/.8).
"""

from __future__ import annotations

import re
from enum import Enum

from pydantic import BaseModel, Field

from cells.ai_flagger import FlaggerConfig, detect_ai_tells
from cells.base import Cell
from cells.content_brief import Platform
from cells.validators import (
    FieldValidator,
    Severity,
    ValidationIssue,
    ValidatorBank,
    no_placeholder,
    non_empty,
)

# Per-platform hard limits the copy must respect (caption chars; hook words).
# Conservative, platform-documented caps; the WARN band keeps captions snappy.
_PLATFORM_CAPTION_MAX = {
    Platform.INSTAGRAM: 2200,
    Platform.FACEBOOK: 5000,
}
_HOOK_MAX_WORDS = 14          # a hook that runs long stops being a hook
_CAPTION_SOFT_MAX_WORDS = 150  # advisory: posts longer than this tend to underperform


class CopyVariant(BaseModel):
    """One hook + caption + CTA execution of the winning angle."""

    pattern: str = Field(description="Which hook/CTA pattern this variant uses (from the library).")
    pillar: str = Field(description="The brand messaging pillar this variant ladders back to.")
    hook: str = Field(description="The scroll-stopping opening line.")
    caption: str = Field(description="The post body, molded into the artist's voice.")
    call_to_action: str = Field(description="A soft, on-voice next step.")


class CopywriterDrafts(BaseModel):
    """A set of distinct on-brand drafts for one winning angle."""

    platform: Platform = Field(description="Target platform (drives length limits).")
    angle: str = Field(description="The winning angle these drafts execute.")
    variants: list[CopyVariant] = Field(description="2-4 DISTINCT drafts (variety, not templates).")


# --------------------------------------------------------------------------- #
# Copywriter-specific validators (iterate the nested variants)
# --------------------------------------------------------------------------- #

_VARIANT_TEXT_FIELDS = ("hook", "caption", "call_to_action")


def _variants(value) -> list[CopyVariant]:
    v = getattr(value, "variants", None)
    return list(v) if isinstance(v, (list, tuple)) else []


def variants_count(minimum: int = 2, maximum: int = 4) -> FieldValidator:
    """Need at least ``minimum`` distinct drafts (variety); beyond ``maximum`` is a WARN."""

    def _fn(value):
        n = len(_variants(value))
        if n < minimum:
            return [ValidationIssue("variants_count", Severity.ERROR,
                                    f"{n} variant(s); need at least {minimum} for variety")]
        if n > maximum:
            return [ValidationIssue("variants_count", Severity.WARN,
                                    f"{n} variant(s); {maximum} is plenty")]
        return []

    return FieldValidator("variants_count", _fn)


def variants_filled() -> FieldValidator:
    """Every variant's hook/caption/CTA must be non-empty and placeholder-free."""

    ne = {f: non_empty(f) for f in _VARIANT_TEXT_FIELDS}
    npl = {f: no_placeholder(f) for f in _VARIANT_TEXT_FIELDS}

    def _fn(value):
        issues: list[ValidationIssue] = []
        for i, var in enumerate(_variants(value)):
            for f in _VARIANT_TEXT_FIELDS:
                for r in (ne[f].check(var).issues + npl[f].check(var).issues):
                    issues.append(ValidationIssue(r.validator, r.severity, f"variant[{i}] {r.message}"))
        return issues

    return FieldValidator("variants_filled", _fn)


def no_ai_tells_in_variants(config: FlaggerConfig = FlaggerConfig()) -> FieldValidator:
    """S3 composition: the AI-flagger detector runs over every variant's text.

    Reuses the deterministic :func:`detect_ai_tells` so the copywriter is held to
    the exact same human-tone bar as the rest of the engine — no em-dash, contrast
    framing, rule-of-three, or generic transitions.
    """

    def _fn(value):
        issues: list[ValidationIssue] = []
        for i, var in enumerate(_variants(value)):
            for f in _VARIANT_TEXT_FIELDS:
                text = getattr(var, f, None)
                if not isinstance(text, str):
                    continue
                for tell in detect_ai_tells(text, config):
                    sev = Severity.ERROR if tell.kind.value != "rule_of_three" else Severity.WARN
                    issues.append(ValidationIssue(
                        "ai_flagger", sev,
                        f"variant[{i}] {f!r}: {tell.message} -> {tell.text!r}"))
        return issues

    return FieldValidator("ai_flagger", _fn)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def hooks_distinct() -> FieldValidator:
    """Anti over-templating: the hooks must be pairwise distinct (normalized)."""

    def _fn(value):
        seen: dict[str, int] = {}
        issues: list[ValidationIssue] = []
        for i, var in enumerate(_variants(value)):
            key = _normalize(getattr(var, "hook", "") or "")
            if not key:
                continue
            if key in seen:
                issues.append(ValidationIssue(
                    "hooks_distinct", Severity.ERROR,
                    f"variant[{i}] hook duplicates variant[{seen[key]}] — vary the structure"))
            else:
                seen[key] = i
        return issues

    return FieldValidator("hooks_distinct", _fn)


def platform_length() -> FieldValidator:
    """Per-platform caption char cap (ERROR) + hook word cap (ERROR) + soft length (WARN)."""

    def _fn(value):
        issues: list[ValidationIssue] = []
        platform = getattr(value, "platform", None)
        cap = _PLATFORM_CAPTION_MAX.get(platform)
        for i, var in enumerate(_variants(value)):
            caption = getattr(var, "caption", "") or ""
            hook = getattr(var, "hook", "") or ""
            if cap is not None and len(caption) > cap:
                issues.append(ValidationIssue(
                    "platform_length", Severity.ERROR,
                    f"variant[{i}] caption {len(caption)} chars > {platform.value} limit {cap}"))
            if len(hook.split()) > _HOOK_MAX_WORDS:
                issues.append(ValidationIssue(
                    "platform_length", Severity.ERROR,
                    f"variant[{i}] hook {len(hook.split())} words > {_HOOK_MAX_WORDS}"))
            if len(caption.split()) > _CAPTION_SOFT_MAX_WORDS:
                issues.append(ValidationIssue(
                    "platform_length", Severity.WARN,
                    f"variant[{i}] caption {len(caption.split())} words; <= {_CAPTION_SOFT_MAX_WORDS} performs better"))
        return issues

    return FieldValidator("platform_length", _fn)


def copywriter_validators(*, config: FlaggerConfig = FlaggerConfig()) -> ValidatorBank:
    """The deterministic gates a copywriter draft set must clear."""
    return ValidatorBank(validators=(
        non_empty("angle"),
        variants_count(),
        variants_filled(),
        hooks_distinct(),
        platform_length(),
        no_ai_tells_in_variants(config),  # S3 AI-flagger
    ))


# --------------------------------------------------------------------------- #
# Instructions (S2 brand-voice composed in)
# --------------------------------------------------------------------------- #

_BASE_RULES = (
    "You are an expert social copywriter for a single tattoo artist. You are given "
    "a SCORED WINNING ANGLE (a pattern/insight proven to work) and must mold it "
    "into THIS artist's voice as several DISTINCT drafts, each a hook + caption + "
    "call-to-action.\n"
    "Hard rules:\n"
    "- The BRAND VOICE WINS. If a pattern fights the artist's voice or hits a "
    "do-not, drop or adapt the pattern — never the voice.\n"
    "- Only use claims from the Approved claims list. If the angle needs a claim "
    "that is not approved, do NOT write it — flag it for the operator.\n"
    "- No AI tells: no em-dashes, no contrast framing ('it's not X, it's Y'), no "
    "rhetorical rule-of-three, no generic transitions ('Moreover', 'In conclusion').\n"
    "- VARY the structure across variants — different hooks and openers, not one "
    "template refilled. Each variant must ladder to a stated brand pillar.\n"
    "- Respect platform length; keep hooks short and CTAs soft and on-voice.\n"
    "- Lead with the client/story or the work, not the studio. Be concrete and human."
)


def build_copywriter_instructions(brand_voice_context: str = "", approved_claims: tuple[str, ...] = ()) -> str:
    """Assemble the cell instructions, composing the S2 brand-voice context in.

    ``brand_voice_context`` is what the brand-voice resolver
    (skills/brand-voice) produced for the tenant; it is placed BEFORE the rules so
    the cell reads the artist's voice before it writes.

    HONEST-EMPTY HARDENING (wwy.7 r8): with no resolved voice / claims, the
    instructions explicitly forbid inventing a sender identity or studio claims —
    mirrors :func:`build_copywriter_email_instructions` (where the fixture-identity
    smoking gun was observed) so the social path can never fabricate one either.
    """
    parts: list[str] = []
    if brand_voice_context.strip():
        parts += ["# BRAND VOICE (source of truth — write as this artist):",
                  brand_voice_context.strip(), ""]
    else:
        parts += [
            "# SENDER IDENTITY: NONE ON FILE.",
            "No brand voice or artist identity is configured for this studio. Write "
            "as 'we' from the studio WITHOUT naming it: do NOT sign with, mention, or "
            "invent ANY artist name, personal name, or studio name — not even a "
            "plausible one.",
            "",
        ]
    if approved_claims:
        parts += ["# Approved claims (the ONLY claims you may state):"]
        parts += [f"- {c}" for c in approved_claims]
        parts += [""]
    else:
        parts += [
            "# Approved claims: NONE on file.",
            "You may make NO specific claim about the studio or artist — no "
            "specialties, no style claims, no awards, no history, no client stories.",
            "",
        ]
    parts += [_BASE_RULES]
    return "\n".join(parts)


def build_copywriter_cell(
    *,
    brand_voice_context: str = "",
    approved_claims: tuple[str, ...] = (),
    config: FlaggerConfig = FlaggerConfig(),
    **overrides,
) -> Cell[CopywriterDrafts]:
    """Build the copywriter cell (hook/CTA drafting from a winning angle).

    Pass ``brand_voice_context`` (from the brand-voice resolver) and
    ``approved_claims`` for the tenant; the harness assembles these at run start.
    """
    params = dict(
        name="copywriter",
        schema=CopywriterDrafts,
        instructions=build_copywriter_instructions(brand_voice_context, approved_claims),
        validators=copywriter_validators(config=config),
    )
    params.update(overrides)
    return Cell(**params)


# =========================================================================== #
# EMAIL mode — cold-outreach copy (1mk.7 handoff; growth owns the sequence)
# =========================================================================== #
#
# growth's outreach policy emits a Touch per step (engine/outreach/schema.py):
#   {index, day_offset, purpose, personalization_brief: tuple[str] (<=2 signals,
#    creepy already stripped), includes_unsubscribe: True}  — channel = email.
# growth builds the sequence + per-touch brief; THIS fills the actual copy for one
# touch: an on-voice {subject, body} grounded in brand-voice (S2), gated by the S3
# AI-flagger + email rules, then the harness jury. Keeps ALL copy in the one gated
# copywriter cell — no second copy path, one S3+jury gate.

# The send/connector layer fills this token with the per-recipient one-click URL
# and adds the RFC 8058 List-Unsubscribe / List-Unsubscribe-Post header. The COPY
# must leave this visible token in the body (CAN-SPAM visible opt-out); the cell
# never invents an unsubscribe URL.
UNSUBSCRIBE_TOKEN = "{{unsubscribe}}"

_SUBJECT_MAX_CHARS = 60        # inbox clipping / deliverability
_EMAIL_BODY_MAX_WORDS = 120    # a cold email that runs long doesn't get read

# social-isms that must never appear in an email (growth ask 2).
_SOCIALISM_RE = re.compile(
    r"#\w+|(?<!\w)@\w+|\blink in bio\b|\bdm me\b|\bdm us\b|\bswipe up\b|\bfollow us\b",
    re.IGNORECASE,
)
# broad emoji / dingbat / arrow ranges — none belong in a cold email.
_EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF☀-➿←-⇿⬀-⯿]")

_EMAIL_FIELDS = ("subject", "body")


class TouchPurpose(str, Enum):
    """Mirrors outreach ``Touch.purpose`` (engine/outreach/schema.py, growth)."""

    INTRO = "intro"
    VALUE_ADD = "value-add"
    SOFT_CTA = "soft-CTA"
    BREAK_UP = "break-up"


class EmailCopy(BaseModel):
    """On-voice cold-outreach email copy for one touch."""

    purpose: TouchPurpose = Field(description="Which touch this is (echoes Touch.purpose).")
    subject: str = Field(description="Subject line: specific, honest, <=60 chars, no clickbait.")
    body: str = Field(description="Short plain-text body, on-voice, ending with the unsubscribe token.")


def _efield(value, field: str) -> str:
    return getattr(value, field, "") or ""


def email_lengths() -> FieldValidator:
    """Subject char cap + body word cap (ERROR). The unsubscribe token is excluded."""

    def _fn(value):
        issues = []
        subj = _efield(value, "subject")
        if len(subj) > _SUBJECT_MAX_CHARS:
            issues.append(ValidationIssue("email_lengths", Severity.ERROR,
                                          f"subject {len(subj)} chars > {_SUBJECT_MAX_CHARS}"))
        words = len(_efield(value, "body").replace(UNSUBSCRIBE_TOKEN, "").split())
        if words > _EMAIL_BODY_MAX_WORDS:
            issues.append(ValidationIssue("email_lengths", Severity.ERROR,
                                          f"body {words} words > {_EMAIL_BODY_MAX_WORDS}"))
        return issues

    return FieldValidator("email_lengths", _fn)


def email_no_socialisms() -> FieldValidator:
    """No hashtags, @handles, social CTAs, or emoji in an email (growth ask 2)."""

    def _fn(value):
        issues = []
        for f in _EMAIL_FIELDS:
            t = _efield(value, f)
            if _SOCIALISM_RE.search(t):
                issues.append(ValidationIssue("email_no_socialisms", Severity.ERROR,
                                              f"{f!r}: no hashtags/@handles/social CTAs in email"))
            if _EMOJI_RE.search(t):
                issues.append(ValidationIssue("email_no_socialisms", Severity.ERROR,
                                              f"{f!r}: no emoji in a cold email"))
        return issues

    return FieldValidator("email_no_socialisms", _fn)


def email_requires_unsubscribe() -> FieldValidator:
    """Body must carry the visible unsubscribe token (send layer fills the URL)."""

    def _fn(value):
        if UNSUBSCRIBE_TOKEN not in _efield(value, "body"):
            return [ValidationIssue("email_requires_unsubscribe", Severity.ERROR,
                                    f"body must include the visible unsubscribe token {UNSUBSCRIBE_TOKEN!r}")]
        return []

    return FieldValidator("email_requires_unsubscribe", _fn)


def email_no_stray_placeholders() -> FieldValidator:
    """no_placeholder over subject+body, but the unsubscribe token is allowed."""

    npl = no_placeholder("t")

    def _fn(value):
        issues = []
        for f in _EMAIL_FIELDS:
            t = _efield(value, f).replace(UNSUBSCRIBE_TOKEN, "")
            for r in npl.check({"t": t}).issues:
                issues.append(ValidationIssue(r.validator, r.severity, f"{f!r} {r.message}"))
        return issues

    return FieldValidator("no_placeholder", _fn)


def email_no_ai_tells(config: FlaggerConfig = FlaggerConfig()) -> FieldValidator:
    """S3 composition: AI-flagger over subject + body (spec scope covers email)."""

    def _fn(value):
        issues = []
        for f in _EMAIL_FIELDS:
            for tell in detect_ai_tells(_efield(value, f), config):
                sev = Severity.ERROR if tell.kind.value != "rule_of_three" else Severity.WARN
                issues.append(ValidationIssue("ai_flagger", sev,
                                              f"{f!r}: {tell.message} -> {tell.text!r}"))
        return issues

    return FieldValidator("ai_flagger", _fn)


def copywriter_email_validators(*, config: FlaggerConfig = FlaggerConfig()) -> ValidatorBank:
    """The deterministic gates a cold-outreach email must clear."""
    return ValidatorBank(validators=(
        non_empty("subject"),
        non_empty("body"),
        email_lengths(),
        email_no_stray_placeholders(),
        email_requires_unsubscribe(),
        email_no_socialisms(),
        email_no_ai_tells(config),   # S3 (covers banned-slop via AF-05 BANNED_SLOP over subject+body)
    ))


_EMAIL_RULES = (
    "You write ONE short cold-outreach EMAIL for a tattoo artist, for a specific "
    "touch in a sequence. Plain text, like a real person — not a brand, not a "
    "template.\n"
    "Rules:\n"
    "- Subject: specific and honest, <=60 characters, no clickbait, no ALL-CAPS, no emoji.\n"
    "- Body: short (<=120 words). Lead with ONE relevant personalization signal from "
    "the brief (max 2 references, never anything creepy). On-voice; only Approved claims.\n"
    "- NO social-isms: no hashtags, no @handles, no 'link in bio' / 'DM me', no emoji.\n"
    "- Match the touch PURPOSE: intro (introduce, earn the next reply), value-add "
    "(give something useful, no ask), soft-CTA (one low-pressure invite), break-up "
    "(graceful last touch, easy to say no).\n"
    "- CAN-SPAM: be honest about who you are; no deception. End the body with a short "
    "visible unsubscribe line containing the EXACT token {{unsubscribe}} — the send "
    "layer fills the one-click link. Never invent an unsubscribe URL.\n"
    "- No AI tells (em-dashes, contrast framing, rule-of-three, generic transitions). "
    "No placeholders other than {{unsubscribe}}."
)


def build_copywriter_email_instructions(brand_voice_context: str = "", approved_claims: tuple[str, ...] = ()) -> str:
    """Assemble EMAIL-mode instructions, composing the S2 brand-voice context in.

    HONEST-EMPTY HARDENING (wwy.7 r8, the smoking gun): a tenant with NO resolved
    brand voice / approved claims (e.g. a real client not yet onboarded) must not
    leave the model unconstrained — the observed failure was drafts signed with a
    FIXTURE studio's artist name ("it's Rae from Ladies First") on a real client's
    customers. With no voice on file the instructions explicitly forbid inventing
    any sender identity; with no approved claims they explicitly forbid any specific
    claim about the studio.
    """
    parts: list[str] = []
    if brand_voice_context.strip():
        parts += ["# BRAND VOICE (source of truth — write as this artist):",
                  brand_voice_context.strip(), ""]
    else:
        parts += [
            "# SENDER IDENTITY: NONE ON FILE.",
            "No brand voice or artist identity is configured for this studio. Write "
            "as 'we' from the studio WITHOUT naming it: do NOT sign with, mention, or "
            "invent ANY artist name, personal name, or studio name — not even a "
            "plausible one. A deterministic guard rejects drafts that assert an "
            "identity that is not on file.",
            "",
        ]
    if approved_claims:
        parts += ["# Approved claims (the ONLY claims you may state):"]
        parts += [f"- {c}" for c in approved_claims]
        parts += [""]
    else:
        parts += [
            "# Approved claims: NONE on file.",
            "You may make NO specific claim about the studio or sender — no "
            "specialties, no style claims, no awards, no history, no client stories, "
            "no 'the people I have tattooed'. Stay with the recipient-grounded facts "
            "in the run prompt.",
            "",
        ]
    parts += [_EMAIL_RULES]
    return "\n".join(parts)


def build_copywriter_email_cell(
    *,
    brand_voice_context: str = "",
    approved_claims: tuple[str, ...] = (),
    config: FlaggerConfig = FlaggerConfig(),
    **overrides,
) -> Cell[EmailCopy]:
    """Build the copywriter EMAIL cell (cold-outreach {subject, body}).

    The harness assembles ``brand_voice_context`` + ``approved_claims`` per tenant;
    the run prompt carries the per-touch brief (purpose, day_offset,
    personalization_brief) from growth's outreach policy.
    """
    params = dict(
        name="copywriter_email",
        schema=EmailCopy,
        instructions=build_copywriter_email_instructions(brand_voice_context, approved_claims),
        validators=copywriter_email_validators(config=config),
    )
    params.update(overrides)
    return Cell(**params)
