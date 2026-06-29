"""Meta webhook comment ingest -> a normalized :class:`CommentEvent`.

Meta delivers Instagram and Facebook comment notifications in DIFFERENT shapes on
the SAME webhook envelope (``{object, entry:[{changes:[{field, value}]}]}``):

* **Instagram** (``object="instagram"``, ``field="comments"``): the comment lives
  in ``value`` as ``{id, text, from:{id,username}, media:{id}, parent_id?}``.
* **Facebook Page** (``object="page"``, ``field="feed"``): the change is a feed
  item; only ``value.item == "comment"`` (with ``verb`` add/edit) is a comment,
  shaped ``{comment_id, message, post_id, from:{id,name}, parent_id?}``.

:func:`parse_comment_payload` walks the envelope and returns one
:class:`CommentEvent` per comment change, **skipping** everything that is not an
added/edited comment (likes, status posts, removes) rather than raising — a single
unrelated change must never drop the batch. A structurally broken envelope (no
``entry`` list) raises :class:`IngestError`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Facebook feed ``verb``s we treat as a live comment to reply to. A removed/hidden
# comment is moderation, not an engagement opportunity, so it is skipped.
_FB_REPLYABLE_VERBS = frozenset({"add", "edit", "edited"})


class IngestError(ValueError):
    """The webhook envelope could not be parsed (missing/!malformed ``entry``)."""


@dataclass(frozen=True)
class CommentEvent:
    """A platform-normalized inbound comment — the unit the engagement path acts on.

    ``platform`` is ``"instagram"`` or ``"facebook"`` (the channel the reply posts
    back to). ``author`` is the display handle/name (falls back to the author id).
    ``post_id``/``parent_id`` are optional (a top-level comment has no parent).
    ``raw`` keeps the original ``value`` object for audit.
    """

    platform: str
    comment_id: str
    post_id: str | None
    author: str
    text: str
    parent_id: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _parse_instagram(value: dict[str, Any]) -> CommentEvent | None:
    """Parse one Instagram ``comments`` change value, or ``None`` if not a comment."""
    comment_id = value.get("id")
    text = value.get("text")
    if not comment_id or not text:
        return None
    src = _as_dict(value.get("from"))
    author = src.get("username") or src.get("id") or "unknown"
    return CommentEvent(
        platform="instagram",
        comment_id=str(comment_id),
        post_id=(_as_dict(value.get("media")).get("id") or value.get("media_id")),
        author=str(author),
        text=str(text),
        parent_id=value.get("parent_id"),
        raw=dict(value),
    )


def _parse_facebook(value: dict[str, Any]) -> CommentEvent | None:
    """Parse one Facebook ``feed`` change value, or ``None`` if it is not a
    replyable comment (likes, status posts, removes are skipped)."""
    if value.get("item") != "comment":
        return None
    verb = value.get("verb")
    if verb is not None and verb not in _FB_REPLYABLE_VERBS:
        return None
    comment_id = value.get("comment_id")
    text = value.get("message")
    if not comment_id or not text:
        return None
    src = _as_dict(value.get("from"))
    author = src.get("name") or src.get("id") or "unknown"
    post_id = value.get("post_id")
    parent_id = value.get("parent_id")
    # On a top-level FB comment the parent_id equals the post_id; only keep a real
    # reply-to-a-comment parent so downstream context isn't misled.
    if parent_id == post_id:
        parent_id = None
    return CommentEvent(
        platform="facebook",
        comment_id=str(comment_id),
        post_id=post_id,
        author=str(author),
        text=str(text),
        parent_id=parent_id,
        raw=dict(value),
    )


def _parse_change(obj: str | None, change: dict[str, Any]) -> CommentEvent | None:
    field_name = change.get("field")
    value = _as_dict(change.get("value"))
    if obj == "instagram" and field_name == "comments":
        return _parse_instagram(value)
    if obj == "page" and field_name == "feed":
        return _parse_facebook(value)
    # Any other object/field (mentions, messages, reactions, ...) is not a comment.
    return None


def parse_comment_payload(payload: dict[str, Any]) -> list[CommentEvent]:
    """Normalize a Meta webhook envelope into the comment events it carries.

    Returns one :class:`CommentEvent` per added/edited comment across all entries,
    skipping non-comment changes. Raises :class:`IngestError` if the envelope has
    no ``entry`` list at all (a malformed request, not just an uninteresting one).
    """
    if not isinstance(payload, dict):
        raise IngestError("webhook payload must be a JSON object")
    entries = payload.get("entry")
    if not isinstance(entries, list):
        raise IngestError("webhook payload has no 'entry' list")
    obj = payload.get("object")

    events: list[CommentEvent] = []
    for entry in entries:
        for change in _as_dict(entry).get("changes") or []:
            event = _parse_change(obj, _as_dict(change))
            if event is not None:
                events.append(event)
    return events
