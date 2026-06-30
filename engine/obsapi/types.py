"""Strawberry GraphQL types — the kkg.4 read contract.

Field names are snake_case in Python; strawberry's default auto-camel-case turns
``tenant_id`` → ``tenantId``, ``review_queue`` → ``reviewQueue`` etc., so these
match ``web/lib/data/queries.ts`` / ``models.ts`` field-for-field.

Enum-like response fields (``type``, ``channel``, ``worker``, ``status``,
``escalation.kind``, ``severity`` …) are plain ``str`` rather than GraphQL enums:
the live DB stores values outside the console's TS unions (e.g. ``esc_kind``
``none``/``mode``), and a hard enum would crash on serialization. ``mappers``
maps them to the contract's UPPERCASE form; the wire JSON is identical.

``Channel`` and ``AutonomyMode`` ARE defined as GraphQL enums because the
console's ``SetAutonomy`` document declares its variables as ``Channel!`` /
``AutonomyMode!`` — those names must exist as enum types for the document to
validate. They are used only as mutation inputs.
"""

from __future__ import annotations

from dataclasses import field
from enum import Enum
from typing import Optional

import strawberry


@strawberry.enum
class Channel(Enum):
    GMAIL = "GMAIL"
    INSTAGRAM = "INSTAGRAM"
    FACEBOOK = "FACEBOOK"


@strawberry.enum
class AutonomyMode(Enum):
    AUTO = "AUTO"
    APPROVE_FIRST = "APPROVE_FIRST"


@strawberry.type
class Escalation:
    kind: str
    label: str


@strawberry.type
class JurorDimScore:
    """Per-juror vote on a single dimension."""

    judge: str
    score: float
    vote: str  # 'pass' | 'fail'


@strawberry.type
class JuryDim:
    label: str
    score: float
    verdict: str = "pass"  # 'pass' | 'fail'
    threshold: float = 0.0
    juror_breakdown: list[JurorDimScore] = field(default_factory=list)


@strawberry.type
class JudgeVote:
    """Per-judge vote. Not selected by the current console document, but exposed
    so the full jury card (per-judge voice/safety/appr) is reachable."""

    judge: str
    family: Optional[str]
    voice: float
    safety: float
    appr: float
    overall: float


@strawberry.type
class JuryDecision:
    confidence: float
    threshold: float
    agreement: str
    dimensions: list[JuryDim]
    judges: list[JudgeVote]
    is_seeded: bool = False
    # B1: generation-stability component of confidence (Phase-5 only; NULL on
    # pre-Phase-5 rows where the probe did not run). NEVER default null→0.
    self_consistency: Optional[float] = None


@strawberry.type
class Gate:
    label: str
    ok: bool


@strawberry.type
class Action:
    id: strawberry.ID
    tenant_id: str
    type: str
    channel: str
    worker: str
    target: str
    created_at: str
    subject: Optional[str]
    context: Optional[str]
    draft: str
    confidence: float
    threshold: float
    escalation: Escalation
    jury: JuryDecision
    gates: list[Gate]
    recommendation: Optional[str]
    idempotency_key: str
    status: str
    # The REAL provider error captured when a send FAILED (actions.last_error),
    # e.g. a Meta/Graph "HTTP 400 #145 …" body. None unless status='failed'.
    # Surfaced verbatim so the operator sees WHY a send failed, never a bare
    # "Failed". Never fabricated — it is the connector's own error string.
    last_error: Optional[str] = None
    judges: list[Judge] = field(default_factory=list)
    is_seeded: bool = False


@strawberry.type
class Outcome:
    label: str
    kind: str


@strawberry.type
class EngagementTile:
    label: str
    value: str


@strawberry.type
class ThreadMessage:
    role: str
    name: Optional[str]
    text: str


@strawberry.type
class CommentItem:
    name: str
    text: str
    auto_replied: bool


@strawberry.type
class ActivityItem:
    """An EXECUTED action (status='sent') for the Activity screen — the Action
    core plus the handoff reasoning/engagement extensions. Mirrors the console's
    ``ActivityItem`` model field-for-field."""

    # --- Action core (identical mapping to Action) ---
    id: strawberry.ID
    tenant_id: str
    type: str
    channel: str
    worker: str
    target: str
    created_at: str
    subject: Optional[str]
    context: Optional[str]
    draft: str
    confidence: float
    threshold: float
    escalation: Escalation
    jury: JuryDecision
    gates: list[Gate]
    recommendation: Optional[str]
    idempotency_key: str
    status: str
    # --- Activity extensions ---
    autonomy: str
    content: str
    outcome: Outcome
    thinking: list[str]
    engagement: list[EngagementTile]
    thread: list[ThreadMessage]
    comments: list[CommentItem]
    # --- v2 observability ---
    run_id: Optional[strawberry.ID]
    trace: Optional[ExecutionTrace]
    judges: list[Judge]
    spans: list[Span]
    links: list[ActivityLink]
    # The REAL provider error on a FAILED send (actions.last_error). A failed
    # action IS executed work, so it appears on the Activity screen; this carries
    # the verbatim Meta/Graph/Gmail error so the detail can say WHY it failed.
    # None unless status='failed'. Never fabricated.
    last_error: Optional[str] = None


@strawberry.type
class RunStep:
    at: str
    text: str
    state: str


@strawberry.type
class Run:
    id: strawberry.ID
    tenant_id: str
    type: str
    trigger: str
    status: str
    started_at: str
    duration: Optional[str]
    auto_count: int
    review_count: int
    retries: int
    idempotency_key: str
    channels: list[str]
    trajectory: list[RunStep]
    note: Optional[str]
    trace_url: Optional[str] = None
    events: list[RunEvent]


@strawberry.type
class FeedEvent:
    id: strawberry.ID
    tenant_id: str
    worker: str
    text: str
    at: str
    chip: Optional[str]
    severity: str
    action_id: Optional[strawberry.ID] = None
    run_id: Optional[strawberry.ID] = None
    decision_id: Optional[strawberry.ID] = None


@strawberry.type
class Kpis:
    autonomy_pct: float
    review_queue_count: int
    outreach_today: int
    complaints_pct: float
    comments_auto: int
    comments_review: int
    posts_published: int
    posts_scheduled: int


@strawberry.type
class SystemHealth:
    email_complaint_rate: float
    email_bounce_rate: float
    gmail_warmup_used: int
    gmail_warmup_cap: int
    ig_publish_used: int
    ig_publish_cap: int
    checkpoint_status: str


@strawberry.type
class AutonomyConfig:
    channel: str
    mode: str
    threshold: float
    held: bool


@strawberry.type
class Tenant:
    id: strawberry.ID
    name: str
    pack: str
    channels: list[str]
    autonomy: list[AutonomyConfig]
    engine_state: str


@strawberry.type
class Overview:
    kpis: Kpis
    attention: list[Action]
    recent_runs: list[Run]
    system_health: SystemHealth
    feed_preview: list[FeedEvent]


@strawberry.type
class ChatMessage:
    id: strawberry.ID
    role: str
    text: str
    label: Optional[str]
    at: str


@strawberry.type
class StudioChatTurn:
    """One persisted Campaign Studio chat turn (P2 interactive Slice 1).

    ``role`` is ``operator`` | ``host``; ``model`` is the real model pin the host
    reply was produced with (``None`` for operator turns). Auto-camel-cases to
    ``sessionId`` / ``createdAt`` to match the FE LiveStudioAdapter document."""

    id: strawberry.ID
    session_id: str
    seq: int
    role: str
    text: str
    model: Optional[str]
    created_at: str


@strawberry.type
class StudioChatExchange:
    """The pair returned by ``sendChatMessage``: the persisted operator turn and
    the persisted REAL host reply (never a canned/echoed message)."""

    operator: StudioChatTurn
    host: StudioChatTurn


@strawberry.input
class ActionFilter:
    type: Optional[str] = None


@strawberry.input
class RunFilter:
    status: Optional[str] = None


@strawberry.input
class FeedFilter:
    worker: Optional[str] = None


@strawberry.input
class CampaignBrief:
    goal: str
    audience: str
    channels: list[str]
    constraints: Optional[str] = None
    hooks: Optional[list[str]] = None


@strawberry.type
class StartCampaignResult:
    run_id: strawberry.ID
    action_ids: list[strawberry.ID]
    status: str


@strawberry.type
class Span:
    """A trace span: a unit of work with timing and output detail."""
    kind: str  # tool|llm|jury|gate|decision
    title: str
    ms: Optional[int]
    detail: str


@strawberry.type
class RunEvent:
    """A top-level event in a run, with nested child spans."""
    worker: str
    text: str
    severity: str  # info|warn|error
    ms: str
    spans: list[Span]
    # B3: ids for the action/run/decision this step produced — set ONLY when the
    # step JSONB carries them (written by harness nodes). None = not yet captured;
    # never synthesized. Enables "Open in Activity" on the Runs screen.
    action_id: Optional[strawberry.ID] = None
    run_id: Optional[strawberry.ID] = None
    decision_id: Optional[strawberry.ID] = None


@strawberry.type
class Judge:
    """Per-judge vote detail: name, aggregated score, pass/fail, per-dimension reasoning."""
    name: str
    score: float
    vote: str  # 'pass'|'fail'
    reasoning: str


@strawberry.type
class ExecutionTrace:
    """Execution metadata: decision ID, latency, model used, tokens."""
    id: strawberry.ID
    latency: str
    model: str
    tokens: str


@strawberry.type
class ActivityLink:
    """A clickable link to the sent post/comment/email."""
    label: str
    target: str
    target_type: str  # POST|COMMENT|EMAIL|DM
