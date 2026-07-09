"""nmh.5 — structured VLM artwork analysis + the no-sensitive-attribute gate.

Hermetic: the pure parse core + the sensitive-attribute backstop are exercised offline
against synthetic tool-use blocks (no network / no key). The gate is the load-bearing
safety property, so it gets the most coverage.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from studio.artwork_vision import (
    ArtworkAnalysis,
    ArtworkVisionError,
    SensitiveAttributeError,
    _ANALYSIS_TOOL,
    analysis_from_tool_use,
    analysis_summary,
    sensitive_attribute_violations,
)

_CLEAN = {
    "style": "black-and-grey realism",
    "motif": "lion",
    "color_mode": "black-and-grey",
    "placement": "forearm",
    "vibe": "bold and symbolic",
    "linework": "smooth shading, crisp outline",
    "complexity": "complex",
    "audience_fit": "someone drawn to strength and protection symbolism",
    "campaign_use": "large custom piece / full-day booking; strength theme",
    "caption_angle": "a statement piece about resilience",
    "style_tags": ["lion", "realism", "black-and-grey"],
}


def _tool_block(payload: dict) -> list:
    return [SimpleNamespace(type="tool_use", name=_ANALYSIS_TOOL["name"], input=payload)]


def test_clean_analysis_parses_and_summarizes():
    a = analysis_from_tool_use(_tool_block(_CLEAN))
    assert isinstance(a, ArtworkAnalysis)
    assert a.motif == "lion" and "realism" in a.style_tags
    s = analysis_summary(a)
    assert "lion" in s and "strength" in s.lower()


def test_missing_tool_block_is_an_error():
    with pytest.raises(ArtworkVisionError):
        analysis_from_tool_use([SimpleNamespace(type="text", text="no tool call")])


def test_schema_validation_failure_is_an_error():
    bad = {**_CLEAN}
    del bad["motif"]  # required field
    with pytest.raises(ArtworkVisionError):
        analysis_from_tool_use(_tool_block(bad))


def test_placement_may_be_null_when_not_visible():
    a = analysis_from_tool_use(_tool_block({**_CLEAN, "placement": None}))
    assert a.placement is None


# --- the no-sensitive-attribute gate --------------------------------------- #
@pytest.mark.parametrize("field,value", [
    ("audience_fit", "young men who want a bold piece"),
    ("audience_fit", "great for women looking for delicate work"),
    ("campaign_use", "target male clients aged 25-40"),
    ("audience_fit", "resonates with a 30 year old"),
    ("caption_angle", "for our Black clients"),
    ("audience_fit", "someone in cancer recovery"),
    ("campaign_use", "aimed at a Christian audience"),
    ("audience_fit", "popular with the LGBTQ community"),
])
def test_sensitive_attribute_inference_is_rejected(field, value):
    payload = {**_CLEAN, field: value}
    a = ArtworkAnalysis.model_validate(payload)
    assert sensitive_attribute_violations(a), (field, value)
    # and the parse path rejects it hard (never returns a tainted analysis)
    with pytest.raises(SensitiveAttributeError):
        analysis_from_tool_use(_tool_block(payload))


@pytest.mark.parametrize("payload", [
    _CLEAN,
    # art-style adjectives + a person-depicting motif are NOT demographic inferences
    {**_CLEAN, "vibe": "masculine, bold energy", "motif": "portrait of a woman"},
    {**_CLEAN, "audience_fit": "wants a strong symbolic statement piece"},
    {**_CLEAN, "motif": "floral half-sleeve", "audience_fit": "loves delicate botanical work"},
])
def test_legitimate_art_descriptions_pass_the_gate(payload):
    a = ArtworkAnalysis.model_validate(payload)
    assert sensitive_attribute_violations(a) == [], payload
