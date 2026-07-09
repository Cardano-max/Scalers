"""Structured VLM artwork analysis (CustomerAcq-nmh.5, spec §3).

Turns ONE artwork image into a typed :class:`ArtworkAnalysis` — style / motif /
color-mode / placement / vibe / linework / complexity / audience-fit / campaign-use /
caption-angle / style-tags — so the agent stack can *reason over* a portfolio, not just
display thumbnails. The analysis feeds the artist memory (tags + summary + embedding)
and the campaign-fit / top-4 selection.

Two hard gates, fail-closed:

* **No fabrication.** The model is instructed to describe ONLY what is visibly present
  in the image; unconfigured environments (no key / no SDK) raise
  :class:`~studio.ingest_vlm.NotConfiguredError` and produce NO analysis — never a
  guessed one. (The image path carries no span-citations, so this is a describe-what-
  you-see gate, the same posture as :mod:`studio.ingest_vlm`'s image path.)
* **No sensitive-attribute inference.** The analysis is about the ARTWORK, never about
  a person. :func:`sensitive_attribute_violations` is a deterministic backstop that
  rejects any analysis inferring a person's protected attributes — age, gender-as-
  audience, ethnicity/race, health/medical status, religion, sexual orientation — most
  importantly in the *audience/campaign* fields (targeting must be theme/interest-based,
  e.g. "drawn to strength & protection symbolism", NEVER "young men"). A violating
  analysis is REJECTED, not stored.

The pure parse core (:func:`analysis_from_tool_use`) is separated from the network call
so it is unit-testable offline against a synthetic tool-use block.
"""

from __future__ import annotations

import base64
import re
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from studio.ingest_vlm import (
    _attr,
    _client,
    _default_model,
    guess_media_type,
)


class ArtworkVisionError(RuntimeError):
    """The analysis could not be produced or violated a hard gate."""


class SensitiveAttributeError(ArtworkVisionError):
    """The analysis inferred a person's protected attribute — rejected, never stored."""


class ArtworkAnalysis(BaseModel):
    """Structured, image-grounded description of ONE artwork (spec §3)."""

    style: str = Field(description="Tattoo style, e.g. 'black-and-grey realism', 'fine-line'.")
    motif: str = Field(description="Subject/motif of the piece, e.g. 'lion', 'floral half-sleeve'.")
    color_mode: str = Field(description="'color' | 'black-and-grey' | 'mixed' — as visibly rendered.")
    placement: str | None = Field(
        default=None, description="Body placement IF visibly determinable, else null (never guessed)."
    )
    vibe: str = Field(description="Mood/vibe of the piece, e.g. 'bold and symbolic', 'delicate, calm'.")
    linework: str = Field(description="Linework character, e.g. 'crisp single-needle', 'bold packed'.")
    complexity: str = Field(description="'simple' | 'moderate' | 'complex' — visual density/detail.")
    audience_fit: str = Field(
        description=(
            "Who this piece resonates with, by INTEREST/THEME ONLY (e.g. 'someone drawn to "
            "strength & protection symbolism'). NEVER a demographic (age/gender/ethnicity)."
        )
    )
    campaign_use: str = Field(
        description="Best campaign use, e.g. 'large custom piece / full-day booking; strength theme'."
    )
    caption_angle: str = Field(description="A concrete caption angle grounded in the piece.")
    style_tags: list[str] = Field(
        default_factory=list,
        description="Short retrieval tags (style + motif keywords), lowercase, e.g. ['lion','realism'].",
    )


# The tool the vision model is forced to call — its input_schema IS the ArtworkAnalysis
# schema, so the model returns structured, validated fields (no free-text parsing).
_ANALYSIS_TOOL: dict[str, Any] = {
    "name": "record_artwork_analysis",
    "description": "Record the structured visual analysis of the artwork image.",
    "input_schema": ArtworkAnalysis.model_json_schema(),
}

_SYSTEM = (
    "You are a senior tattoo-marketing art director analyzing ONE artwork image for a "
    "studio's portfolio memory. Describe ONLY what is visibly present in the image — "
    "the tattoo/art itself: style, motif, color, linework, complexity, vibe. Do NOT "
    "invent details you cannot see; if a field is not visibly determinable, say so "
    "plainly or leave placement null.\n\n"
    "HARD RULE — NO SENSITIVE-ATTRIBUTE INFERENCE: this analysis is about the ARTWORK, "
    "never about a person. If a person is visible, describe ONLY the tattoo/art on them, "
    "never the person's age, gender, ethnicity/race, body, health, religion, or any "
    "protected attribute. 'audience_fit' and 'campaign_use' MUST target by INTEREST / "
    "THEME (e.g. 'drawn to strength & protection symbolism', 'wants a bold statement "
    "piece'), NEVER by demographic (never 'young men', 'women', an age, or an ethnicity)."
)

_USER = (
    "Analyze this artwork and record the structured analysis via the tool. Ground every "
    "field in what is visibly shown. Theme/interest-based audience only — no demographics."
)


# --------------------------------------------------------------------------- #
# No-sensitive-attribute backstop gate
# --------------------------------------------------------------------------- #
# Two tiers, to separate a PERSON's protected attribute (forbidden) from a legitimate
# ART descriptor (allowed — tattoo analysis is full of "black-and-grey", religious
# iconography, pride flash, portraits of people).
#
# TIER 1 — UNCONDITIONAL: terms that are essentially never an art descriptor and always
# signal a person's protected attribute. Rejected in ANY field.
_UNCONDITIONAL: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ethnicity", re.compile(
        r"\b(?:caucasian|latino|latina|latinx|hispanic|african[\s-]?american|indigenous|"
        r"middle[\s-]?eastern|ethnicity|\brace\b|skin[\s-]?tone|complexion)\b", re.I)),
    ("health", re.compile(
        r"\b(?:mastectomy|cancer|pregnan\w*|disabled|disability|\billness\b|chronically ill|"
        r"terminally ill)\b", re.I)),
    ("orientation", re.compile(r"\b(?:heterosexual|homosexual)\b", re.I)),
)

# TIER 2 — DEMOGRAPHIC-AS-TARGETING: terms that ARE legitimate art descriptors on their
# own ("black-and-grey", "a woman's portrait", "buddhist mandala", "pride flash") but
# become a protected-attribute inference when used to TARGET an audience. Rejected ONLY
# in the audience/targeting fields, and (for the ambiguous ones) only next to an
# audience/person noun.
_AUDIENCE_FIELDS = ("audience_fit", "campaign_use", "caption_angle")
_PERSON = r"(?:man|woman|men|women|people|person|client|customer|guy|lady|folks|community|audience|market)"
_TARGETING: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("age", re.compile(
        r"\b(?:\d{1,2}[\s-]?(?:years?[\s-]?old|yo)\b|aged?\s+\d|young|elderly|"
        r"middle[\s-]?aged|teenage|teen|adolescent|millennial|gen[\s-]?z|boomer|"
        r"senior citizen)\b", re.I)),
    ("gender", re.compile(
        r"\b(?:for\s+(?:men|women|males|females|guys|ladies|gentlemen)\b"
        r"|(?:male|female|men|women|guys|ladies|gentlemen)\s+" + _PERSON + r")", re.I)),
    ("ethnicity", re.compile(
        r"\b(?:black|white|asian|brown|native)\s+" + _PERSON + r"s?\b", re.I)),
    ("religion", re.compile(
        r"\b(?:christian|muslim|islam(?:ic)?|jewish|hindu|buddhist|catholic|religious)\s+"
        + _PERSON + r"s?\b", re.I)),
    ("orientation", re.compile(
        r"\b(?:gay|lesbian|lgbtq?\+?|queer)\s+" + _PERSON + r"s?\b", re.I)),
)


def sensitive_attribute_violations(analysis: ArtworkAnalysis) -> list[str]:
    """Deterministic backstop: every sensitive-attribute inference in ``analysis``.

    Tier 1 (ethnicity/health/orientation terms that are never art) is rejected in any
    field; tier 2 (age/gender/ethnicity/religion/orientation used to TARGET) is rejected
    only in the audience/targeting fields, and only next to an audience/person noun — so
    a legitimate art descriptor ("black-and-grey", a portrait motif, religious iconography,
    pride flash) is never falsely rejected. Empty == clean."""
    data = analysis.model_dump()

    def _text(value: Any) -> str:
        return " ".join(value) if isinstance(value, list) else str(value or "")

    violations: list[str] = []
    for group, pattern in _UNCONDITIONAL:
        for field, value in data.items():
            m = pattern.search(_text(value))
            if m:
                violations.append(_msg(group, field, m.group(0)))
    for group, pattern in _TARGETING:
        for field in _AUDIENCE_FIELDS:
            m = pattern.search(_text(data.get(field)))
            if m:
                violations.append(_msg(group, field, m.group(0)))
    return violations


def _msg(group: str, field: str, matched: str) -> str:
    return (
        f"sensitive-attribute inference [{group}] in field {field!r}: matched {matched!r} "
        "— targeting/description must be theme/interest-based, never a person's protected "
        "attribute"
    )


def analysis_from_tool_use(blocks: Any) -> ArtworkAnalysis:
    """Parse the model's forced tool-use block into a validated :class:`ArtworkAnalysis`,
    then run the sensitive-attribute gate. Pure — no network. Raises
    :class:`ArtworkVisionError` if no tool-use block is present or it fails schema
    validation; :class:`SensitiveAttributeError` if the gate trips (never returns a
    tainted analysis)."""
    tool_input: dict[str, Any] | None = None
    for b in blocks or []:
        if _attr(b, "type") == "tool_use" and _attr(b, "name") == _ANALYSIS_TOOL["name"]:
            tool_input = _attr(b, "input")
            break
    if tool_input is None:
        raise ArtworkVisionError(
            "model did not return the structured artwork analysis (no tool_use block)"
        )
    try:
        analysis = ArtworkAnalysis.model_validate(tool_input)
    except ValidationError as exc:
        raise ArtworkVisionError(f"artwork analysis failed schema validation: {exc}") from exc

    violations = sensitive_attribute_violations(analysis)
    if violations:
        raise SensitiveAttributeError("; ".join(violations))
    return analysis


def analyze_artwork(
    data: bytes,
    *,
    media_type: str | None = None,
    filename: str | None = None,
    model: str | None = None,
    client: Any = None,
    max_tokens: int = 1024,
) -> ArtworkAnalysis:
    """Analyze ONE artwork image (bytes) into a validated :class:`ArtworkAnalysis`.

    ``media_type`` is inferred from ``filename`` when omitted. Fail-closed: an
    unconfigured environment raises :class:`NotConfiguredError` (never a fabricated
    analysis); a non-image type raises :class:`ArtworkVisionError`; a sensitive-attribute
    inference raises :class:`SensitiveAttributeError`."""
    mt = media_type or (guess_media_type(filename or "") if filename else None)
    if not mt or not mt.startswith("image/"):
        raise ArtworkVisionError(
            f"unsupported artwork type media_type={mt!r} (need PNG/JPEG/WebP/GIF)"
        )
    client = client or _client()  # raises NotConfiguredError when unconfigured
    model = model or _default_model()
    b64 = base64.standard_b64encode(bytes(data)).decode("ascii")
    message = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=_SYSTEM,
        tools=[_ANALYSIS_TOOL],
        tool_choice={"type": "tool", "name": _ANALYSIS_TOOL["name"]},
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": mt, "data": b64}},
                {"type": "text", "text": _USER},
            ],
        }],
    )
    return analysis_from_tool_use(_attr(message, "content", []) or [])


def analysis_summary(analysis: ArtworkAnalysis) -> str:
    """A compact human summary + the retrieval text embedded in artist memory."""
    return (
        f"{analysis.style} — {analysis.motif}. {analysis.color_mode}, {analysis.linework}, "
        f"{analysis.complexity}. Vibe: {analysis.vibe}. Best for: {analysis.campaign_use}. "
        f"Audience: {analysis.audience_fit}."
    )
