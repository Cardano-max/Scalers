"""Canonical PostDraft / MediaSpec schema (Phase-3 ADR PR #38).

The typed output of the copywriter/draft cell (a9m.5) and the input the media/
format validators (a9m.6) check. Defined here as the one shared contract so the
draft cell and the validators build to identical types (the ADR is the source).

Phase-3 mock generates **no real media** — ``MediaSpec.brief`` describes what the
asset should show; the validators check the *spec* (kind/aspect/duration) +
caption/hashtags. Real-media facts (JPEG bytes, codec/container, file size) are
the real-publisher's job (Phase-6), not representable in this spec.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from cells.content_brief import Platform


class MediaKind(str, Enum):
    """The asset kind a draft implies (also referenced by a9m.4's Angle.format_hint)."""

    IMAGE = "image"
    REEL = "reel"
    CAROUSEL = "carousel"
    TEXT = "text"


class MediaSpec(BaseModel):
    """The creative spec for a post (no real asset in Phase-3 — see ``brief``)."""

    model_config = {"frozen": True}

    kind: MediaKind
    aspect_ratio: str | None = Field(default=None, description='None for text; e.g. "4:5", "9:16".')
    duration_s: float | None = Field(default=None, description="REEL only; seconds.")
    brief: str = Field(description="What the creative should show (mock: no real asset).")


class PostDraft(BaseModel):
    """One organic social post, ready for media/format validation + scoring."""

    model_config = {"frozen": True}

    platform: Platform
    caption: str
    hashtags: list[str] = Field(default_factory=list, description="Without the '#' sign.")
    call_to_action: str
    media: MediaSpec
