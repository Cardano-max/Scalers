"""Canonical post schemas — the ONE MediaKind / MediaSpec / PostDraft (Phase-3).

Single source of truth so there is exactly one ``MediaKind`` enum object across
the engine. Two distinct enums (the old ``cells.ideate.MediaKind`` +
``cells.post_draft.MediaKind``) broke identity checks: a9m.6's media validator
does ``kind is MediaKind.TEXT``, which FAILS when ``kind`` comes from the other
enum — fail-closed → a valid TEXT post wrongly fails its media gate, and the
Phase-3 e2e (a9m.9) would hit it.

Fix: ``cells.ideate`` (Angle.format_hint) and ``cells.post_draft`` (validators) +
the draft cell (a9m.5) all import these names from HERE. No second enum anywhere,
so every identity check sees the same object. Shape is the ADR PR #38 contract.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from cells.content_brief import Platform


class MediaKind(str, Enum):
    """The asset kind a draft implies (Angle.format_hint + MediaSpec.kind)."""

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
