"""Example typed cell: the content brief.

A content brief is the strategist's structured hand-off to the copywriter: what
to post, why, and the guardrails. It is a good first cell because every field is
machine-checkable, so it exercises both schema validation (types/enums) and the
deterministic validator bank (lengths, banned phrases, placeholders).

Construct the cell with :func:`build_content_brief_cell` and run it with a
prompt describing the campaign context. The cell runs at temperature 0 against
the pinned default model.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from cells.base import Cell
from cells.validators import (
    ValidatorBank,
    banned_phrases,
    max_items,
    no_placeholder,
    non_empty,
    word_count_between,
)


class Platform(str, Enum):
    """Where the post will run."""

    INSTAGRAM = "instagram"
    FACEBOOK = "facebook"


class ContentBrief(BaseModel):
    """A structured brief for one organic social post."""

    headline: str = Field(description="Punchy internal title for the post idea.")
    platform: Platform = Field(description="Target platform for the post.")
    angle: str = Field(description="The strategic angle / hook in one sentence.")
    caption: str = Field(description="Draft caption in the brand voice.")
    hashtags: list[str] = Field(default_factory=list, description="Suggested hashtags, without the # sign.")
    call_to_action: str = Field(description="What the viewer should do next.")


def content_brief_validators() -> ValidatorBank:
    """The deterministic gates a content brief must clear.

    ERROR issues trigger a repair retry; the brief never flows downstream until
    it clears them (or the cell fails on a code path).
    """
    return ValidatorBank(
        validators=(
            non_empty("headline"),
            non_empty("angle"),
            non_empty("caption"),
            non_empty("call_to_action"),
            word_count_between("caption", 5, 150),
            no_placeholder("caption"),
            no_placeholder("headline"),
            banned_phrases("caption"),
            banned_phrases("headline"),
            # Advisory: many hashtags is a smell, not a hard failure.
            max_items("hashtags", 10),
        )
    )


_INSTRUCTIONS = (
    "You are a social-media strategist. Given campaign context, produce a single "
    "content brief for one organic post. Write the caption in a concrete, human "
    "brand voice — no AI boilerplate, no placeholders. Fill every field."
)


def build_content_brief_cell(**overrides) -> Cell[ContentBrief]:
    """Build the content-brief cell.

    Keyword overrides are passed through to :class:`~cells.base.Cell` (e.g.
    ``model`` or ``text_output``) — useful for tests and for routing a cell to a
    cheaper model.
    """
    params = dict(
        name="content_brief",
        schema=ContentBrief,
        instructions=_INSTRUCTIONS,
        validators=content_brief_validators(),
    )
    params.update(overrides)
    return Cell(**params)
