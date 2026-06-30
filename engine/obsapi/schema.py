"""Strawberry schema — Query + Mutation, mounted at ``POST /graphql``.

Resolvers are async and offload the (synchronous psycopg) DB work to a thread via
``asyncio.to_thread`` so the event loop is never blocked. Field/argument names
auto-camel-case to match ``web/lib/data/queries.ts`` exactly.

``action``/``run`` take an OPTIONAL ``tenantId`` (the console sends only ``id``):
when supplied it is enforced; otherwise the row is fetched by its unique id.
"""

from __future__ import annotations

import asyncio
import datetime
from typing import Optional

import strawberry

from . import repo
from studio import orchestrator
from studio import chat as studio_chat
from studio.chat_store import ChatTurnRecord
from .types import (
    Action,
    ActionFilter,
    ActivityItem,
    AutonomyConfig,
    AutonomyMode,
    CampaignBrief,
    CampaignSpec,
    Channel,
    ChatMessage,
    FeedEvent,
    FeedFilter,
    Kpis,
    Overview,
    Run,
    RunFilter,
    StartCampaignResult,
    StudioChatExchange,
    StudioChatTurn,
    SystemHealth,
    Tenant,
)


def _to_chat_turn(rec: ChatTurnRecord) -> StudioChatTurn:
    return StudioChatTurn(
        id=strawberry.ID(rec.id),
        session_id=rec.session_id,
        seq=rec.seq,
        role=rec.role,
        text=rec.text,
        model=rec.model,
        created_at=rec.created_at,
    )


@strawberry.type
class Query:
    @strawberry.field
    async def tenant(self, id: strawberry.ID) -> Optional[Tenant]:
        return await asyncio.to_thread(repo.tenant, str(id))

    @strawberry.field
    async def overview(self, tenant_id: strawberry.ID) -> Overview:
        return await asyncio.to_thread(repo.overview, str(tenant_id))

    @strawberry.field
    async def kpis(self, tenant_id: strawberry.ID) -> Kpis:
        return await asyncio.to_thread(repo.kpis, str(tenant_id))

    @strawberry.field
    async def review_queue(
        self, tenant_id: strawberry.ID, filter: Optional[ActionFilter] = None
    ) -> list[Action]:
        type_filter = filter.type if filter else None
        return await asyncio.to_thread(repo.review_queue, str(tenant_id), type_filter)

    @strawberry.field
    async def action(
        self, id: strawberry.ID, tenant_id: Optional[strawberry.ID] = None
    ) -> Optional[Action]:
        tid = str(tenant_id) if tenant_id else None
        return await asyncio.to_thread(repo.action, str(id), tid)

    @strawberry.field
    async def runs(
        self, tenant_id: strawberry.ID, filter: Optional[RunFilter] = None
    ) -> list[Run]:
        status_filter = filter.status if filter else None
        return await asyncio.to_thread(repo.runs, str(tenant_id), status_filter)

    @strawberry.field
    async def run(
        self, id: strawberry.ID, tenant_id: Optional[strawberry.ID] = None
    ) -> Optional[Run]:
        tid = str(tenant_id) if tenant_id else None
        return await asyncio.to_thread(repo.run, str(id), tid)

    @strawberry.field
    async def campaign_spec(self, run_id: strawberry.ID) -> Optional[CampaignSpec]:
        """The per-campaign spec doc for a run (honest-null when absent)."""
        return await asyncio.to_thread(repo.campaign_spec, str(run_id))

    @strawberry.field
    async def feed(
        self,
        tenant_id: strawberry.ID,
        filter: Optional[FeedFilter] = None,
        after: Optional[strawberry.ID] = None,
        limit: Optional[int] = None,
    ) -> list[FeedEvent]:
        worker_filter = filter.worker if filter else None
        return await asyncio.to_thread(
            repo.feed,
            str(tenant_id),
            limit if limit is not None else 100,
            worker_filter,
            str(after) if after else None,
        )

    @strawberry.field
    async def system_health(self, tenant_id: strawberry.ID) -> SystemHealth:
        return await asyncio.to_thread(repo.system_health, str(tenant_id))

    @strawberry.field
    async def activity(
        self, tenant_id: strawberry.ID, filter: Optional[ActionFilter] = None
    ) -> list[ActivityItem]:
        type_filter = filter.type if filter else None
        return await asyncio.to_thread(repo.activity, str(tenant_id), type_filter)

    @strawberry.field
    async def activity_item(self, id: strawberry.ID) -> Optional[ActivityItem]:
        return await asyncio.to_thread(repo.activity_item, str(id))

    @strawberry.field
    async def studio_chat_history(self, session_id: str) -> list[StudioChatTurn]:
        """The ordered Campaign Studio conversation for a session (P2 Slice 1)."""
        rows = await studio_chat.chat_history(str(session_id))
        return [_to_chat_turn(r) for r in rows]


@strawberry.type
class Mutation:
    @strawberry.mutation
    async def approve_action(
        self, id: strawberry.ID, idempotency_key: str
    ) -> Optional[Action]:
        return await asyncio.to_thread(repo.approve_action, str(id), idempotency_key)

    @strawberry.mutation
    async def reject_action(
        self, id: strawberry.ID, reason: Optional[str] = None
    ) -> Optional[Action]:
        return await asyncio.to_thread(repo.reject_action, str(id), reason)

    @strawberry.mutation
    async def edit_action_draft(
        self, id: strawberry.ID, draft: str
    ) -> Optional[Action]:
        return await asyncio.to_thread(repo.edit_action_draft, str(id), draft)

    @strawberry.mutation
    async def regenerate_action(self, id: strawberry.ID) -> Optional[Action]:
        return await asyncio.to_thread(repo.regenerate_action, str(id))

    # --- secondary surface (kept minimal so the live console never hits an
    # unknown-field error on a dial/command interaction) ---------------------
    @strawberry.mutation
    async def set_engine_state(self, tenant_id: strawberry.ID, paused: bool) -> str:
        # No engine_state column to persist; echo the requested state.
        return "PAUSED" if paused else "RUNNING"

    @strawberry.mutation
    async def set_autonomy(
        self,
        tenant_id: strawberry.ID,
        channel: Channel,
        mode: AutonomyMode,
        threshold: float,
    ) -> AutonomyConfig:
        # 439 HOLD gate: the channel stays held; the dial is request-only and the
        # backend never reports AUTO as actually active.
        return AutonomyConfig(
            channel=channel.value, mode=mode.value, threshold=threshold, held=True
        )

    @strawberry.mutation
    async def send_command(
        self, tenant_id: strawberry.ID, text: str
    ) -> ChatMessage:
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        return ChatMessage(
            id=strawberry.ID("cmd_ack"),
            role="ASSISTANT",
            text=f"Acknowledged: {text}",
            label=None,
            at=now,
        )

    @strawberry.mutation
    async def send_chat_message(
        self, session_id: str, text: str
    ) -> StudioChatExchange:
        """P2 interactive Slice 1: persist the operator turn, call the REAL
        studio-host agent with the conversation so far, persist the host turn,
        and return both. The host reply is a real LLM call — never canned."""
        operator, host = await studio_chat.send_chat_message(str(session_id), text)
        return StudioChatExchange(
            operator=_to_chat_turn(operator), host=_to_chat_turn(host)
        )

    @strawberry.mutation
    async def start_campaign(
        self, tenant_id: strawberry.ID, brief: CampaignBrief
    ) -> StartCampaignResult:
        # Build the brief dict from the input type
        brief_dict = {
            "goal": brief.goal,
            "audience": brief.audience,
            "channels": brief.channels,
        }
        if brief.constraints is not None:
            brief_dict["constraints"] = brief.constraints
        if brief.hooks is not None:
            brief_dict["hooks"] = brief.hooks

        # Call the orchestrator to start the campaign
        result = await asyncio.to_thread(
            orchestrator.start_campaign, str(tenant_id), brief_dict
        )

        # Return the result as a StartCampaignResult type
        return StartCampaignResult(
            run_id=strawberry.ID(result["run_id"]),
            action_ids=[strawberry.ID(aid) for aid in result["action_ids"]],
            status="PENDING",
        )


schema = strawberry.Schema(query=Query, mutation=Mutation)
