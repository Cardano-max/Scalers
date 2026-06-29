"""Comment auto-reply ENGAGEMENT path (team-lead item #4, approve-first / gated).

The inbound-comment slice of the engine: a Meta webhook comment (Instagram or
Facebook) -> a normalized :class:`~engagement.ingest.CommentEvent` -> triage
(classify + propose an on-voice reply draft) -> a REAL cross-family decision
(``autonomy=HOLD``) -> a PENDING reply :mod:`actions` row in the review queue.

**Nothing sends here.** Every reply is gated behind the operator's Approve in the
console; the connector posts the reply later (built separately). The whole path
can be driven WITHOUT a live webhook via :func:`engagement.simulate.simulate_comment_event`,
since live Meta events need a public tunnel + a valid (currently expired) token.
"""

from __future__ import annotations

from engagement.ingest import CommentEvent, IngestError, parse_comment_payload
from engagement.triage import (
    ReplyProposal,
    TriageCategory,
    TriageResult,
    classify_comment,
    triage_comment,
)

__all__ = [
    "CommentEvent",
    "IngestError",
    "parse_comment_payload",
    "ReplyProposal",
    "TriageCategory",
    "TriageResult",
    "classify_comment",
    "triage_comment",
]
