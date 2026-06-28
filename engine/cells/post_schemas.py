"""Phase-3 POST output schemas (a9m.1 ADR Decision 1).

The typed artifact the Draft (Create) cell (a9m.5) produces and a9m.6
(media/format validation) + a9m.7 (Check&Score) consume — so raw model text never
flows downstream (HARN-02). Kept in a leaf module (only pydantic + the existing
``cells.content_brief.Platform``) so both the harness and the cells can import it
with no cycle.

``MediaKind`` is imported from the canonical ``cells.ideate`` (a9m.4, merged —
super-confirmed) and re-exported here; a9m.5 does not duplicate it. ``Angle`` stays
a9m.4-owned — the Draft cell consumes its *fields* (hook/rationale/format_hint),
not the class.

FOLLOW-UP (growth, post-merge): ``PostDraft`` + ``MediaSpec`` are also defined in
``cells/post_draft.py`` (a9m.6, PR #71). To avoid a cross-PR rebase conflict now,
the consolidation to ONE pair lands as a single fresh commit after whichever of
a9m.5 / a9m.6 merges first (growth owns it); both definitions are field-identical
to the ADR shape meanwhile.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from cells.content_brief import Platform  # instagram | facebook (re-exported)
from cells.ideate import MediaKind  # canonical MediaKind (a9m.4, super-confirmed) — re-exported

__all__ = ["Platform", "MediaKind", "MediaSpec", "PostDraft"]


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
