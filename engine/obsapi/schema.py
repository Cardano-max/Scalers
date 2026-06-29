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
from .types import (
    Action,
    ActionFilter,
    AutonomyConfig,
    AutonomyMode,
    Channel,
    ChatMessage,
    FeedEvent,
    FeedFilter,
    Kpis,
    Overview,
    Run,
    RunFilter,
    SystemHealth,
    Tenant,
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


schema = strawberry.Schema(query=Query, mutation=Mutation)
