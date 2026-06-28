"""Phase-3 POST output schemas (a9m.1 ADR Decision 1).

The typed artifact the Draft (Create) cell (a9m.5) produces and a9m.6
(media/format validation) + a9m.7 (Check&Score) consume — so raw model text never
flows downstream (HARN-02). Kept in a leaf module (only pydantic + the existing
``cells.content_brief.Platform``) so both the harness and the cells can import it
with no cycle.

RECONCILE-ON-MERGE: ``MediaKind`` is also declared on the a9m.4 branch
(``cells/ideate.py``, for ``Angle.format_hint``). Both branches are pre-merge; on
reconcile, ideate should import ``MediaKind`` from here (one definition, ADR
"one schema" rule). ``Angle`` itself stays a9m.4-owned — the Draft cell consumes
its *fields* (hook/rationale/format_hint), not the class, so a9m.5 does not
duplicate it.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from cells.content_brief import Platform  # instagram | facebook (re-exported)

__all__ = ["Platform", "MediaKind", "MediaSpec", "PostDraft"]


class MediaKind(str, Enum):
    """The creative type a post carries (drives a9m.6 per-kind validation)."""

    IMAGE = "image"
    REEL = "reel"
    CAROUSEL = "carousel"
    TEXT = "text"  # text-only post; no media asset


class MediaSpec(BaseModel):
    """The intended creative for a draft (ADR Decision 1).

    Phase-3 generates no real asset — the ``brief`` + spec are what a9m.6 validates
    and the console shows. a9m.6 gate (POST-02): REEL ⇒ 9:16 + 5–90s; IMAGE/CAROUSEL
    ⇒ an aspect ratio; TEXT ⇒ no media.
    """

    model_config = ConfigDict(extra="forbid")

    kind: MediaKind
    aspect_ratio: str | None = None  # None for TEXT; "9:16" reel, "1:1"/"4:5" image/carousel
    duration_s: float | None = None  # REEL only
    brief: str = Field(description="What the creative should show (no real asset in Phase 3).")


class PostDraft(BaseModel):
    """A complete, on-voice organic post draft — the Draft cell's typed output."""

    model_config = ConfigDict(extra="forbid")

    platform: Platform
    caption: str
    hashtags: list[str] = Field(default_factory=list)  # stored without the leading '#'
    call_to_action: str
    media: MediaSpec
