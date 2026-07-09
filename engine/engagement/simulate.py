"""Drive the engagement path WITHOUT a live webhook.

Live Meta comment events need a public webhook tunnel + a valid IG/FB token (the
demo token is expired). :func:`simulate_comment_event` synthesizes a normalized
:class:`~engagement.ingest.CommentEvent` straight from ``(platform, text, author)``
and runs the full handler — triage -> HOLD decision -> PENDING reply action — so
the whole slice is exercisable end-to-end against real Postgres for the demo.

This is NOT a fake live event: it bypasses *only* the unavailable transport
(tunnel + token), then runs the identical, real downstream path (real decision
record, real pending action, no send).
"""

from __future__ import annotations

import hashlib

from engagement.handler import EngagementResult, handle_comment_event_sync
from engagement.ingest import CommentEvent


def _synthetic_comment_id(platform: str, author: str, text: str) -> str:
    """A stable, deterministic comment id (so re-running the same simulated comment
    is idempotent: same decision id, same action idempotency key)."""
    digest = hashlib.sha1(f"{platform}:{author}:{text}".encode("utf-8")).hexdigest()[:16]
    prefix = "ig" if platform == "instagram" else "fb"
    return f"{prefix}_sim:{digest}"


def synthesize_comment_event(
    platform: str,
    text: str,
    author: str = "commenter",
    *,
    comment_id: str | None = None,
    post_id: str | None = None,
    parent_id: str | None = None,
) -> CommentEvent:
    """Build a normalized :class:`CommentEvent` as if it had arrived from a webhook."""
    platform = "instagram" if platform.lower().startswith("i") else "facebook"
    return CommentEvent(
        platform=platform,
        comment_id=comment_id or _synthetic_comment_id(platform, author, text),
        post_id=post_id or (f"{'ig' if platform == 'instagram' else 'fb'}_sim_post"),
        author=author,
        text=text,
        parent_id=parent_id,
        raw={"simulated": True},
    )


def simulate_comment_event(
    platform: str,
    text: str,
    author: str = "commenter",
    *,
    comment_id: str | None = None,
    post_id: str | None = None,
    parent_id: str | None = None,
    **handler_kwargs,
) -> EngagementResult:
    """Synthesize a comment and run the engagement handler. Extra keyword args are
    forwarded to :func:`~engagement.handler.handle_comment_event` (e.g. ``tenant_id``,
    ``decision_store``, ``action_recorder``, ``reply_generator``, ``judge_runner``)."""
    event = synthesize_comment_event(
        platform, text, author, comment_id=comment_id, post_id=post_id, parent_id=parent_id
    )
    return handle_comment_event_sync(event, **handler_kwargs)
