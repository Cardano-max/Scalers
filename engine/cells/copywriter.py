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
    """
    parts: list[str] = []
    if brand_voice_context.strip():
        parts += ["# BRAND VOICE (source of truth — write as this artist):",
                  brand_voice_context.strip(), ""]
    if approved_claims:
        parts += ["# Approved claims (the ONLY claims you may state):"]
        parts += [f"- {c}" for c in approved_claims]
        parts += [""]
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
