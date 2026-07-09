"""One-shot DB bootstrap — provision EVERY engine table on a fresh cluster.

The engine's stores each own their DDL behind lazy ``setup()``/``ensure_schema()``
calls, so on a fresh machine tables only appear when (if) their code path first
runs — and a campaign run that crashes early leaves later stores unprovisioned
(the recurring "relation X does not exist" failure on restart/fresh-clone).

This script makes fresh-machine bring-up deterministic: run it once after
``infra/initdb/*.sql`` and every table the engine can touch exists.

Run:  ENGINE_DATABASE_URL=postgresql://... uv run python bootstrap_db.py
Idempotent: every store uses CREATE TABLE IF NOT EXISTS.
"""

from __future__ import annotations

import os
import sys


def _dsn() -> str:
    dsn = os.environ.get("ENGINE_DATABASE_URL")
    if not dsn:
        sys.exit("ENGINE_DATABASE_URL is required")
    return dsn


def main() -> int:
    dsn = _dsn()
    ok: list[str] = []
    failed: list[tuple[str, str]] = []

    def step(name: str, fn) -> None:
        try:
            fn()
            ok.append(name)
        except Exception as exc:  # noqa: BLE001 — report + continue, never abort the sweep
            failed.append((name, f"{type(exc).__name__}: {exc}"))

    # --- module-level ensure/setup functions -------------------------------- #
    from actions import audit as actions_audit
    from actions import store as actions_store
    from research import sources_store
    from studio import artifacts as studio_artifacts
    from studio import blueprint_store, campaign_plan_store, campaign_spec_store
    from studio import campaign_examples_store, conversations, documents, durable_run
    from tenants import store as tenants_store

    step("actions.store", lambda: actions_store.ensure_schema(dsn))
    step("actions.audit", lambda: actions_audit.ensure_schema(dsn))
    step("research.sources_store", lambda: sources_store.ensure_schema(dsn))
    step("studio.artifacts", lambda: studio_artifacts.ensure_schema(dsn))
    step("studio.documents", lambda: documents.ensure_schema(dsn))
    step("studio.conversations", lambda: conversations.ensure_schema(dsn))
    step("studio.durable_run", lambda: durable_run.ensure_schema(dsn))
    step("studio.blueprint_store", lambda: blueprint_store.setup(dsn))
    step("studio.campaign_plan_store", lambda: campaign_plan_store.setup(dsn))
    step("studio.campaign_spec_store", lambda: campaign_spec_store.setup(dsn))
    step("studio.campaign_examples_store", lambda: campaign_examples_store.ensure_schema(dsn))
    step("tenants.store", lambda: tenants_store.ensure_schema(dsn))

    # --- class-based stores -------------------------------------------------- #
    from archetypes.registry import ArchetypeStore
    from autonomy.store import PostgresDecisionStore
    from harness.runstore import PostgresRunStore
    from memory.store import MemoryStore
    from proactive.schedule_ledger import ScheduleLedger
    from studio.chat_store import PostgresChatStore
    from team.store import TeamStore

    step("autonomy.store", lambda: PostgresDecisionStore(dsn).setup())
    step("team.store (agent_runs)", lambda: TeamStore(dsn).setup())
    step("harness.runstore (runs)", lambda: PostgresRunStore(dsn).setup())
    step("archetypes.registry", lambda: ArchetypeStore(dsn).setup())
    step("memory.store", lambda: MemoryStore(dsn).ensure_schema())
    step("studio.chat_store", lambda: PostgresChatStore(dsn).setup())
    step("proactive.schedule_ledger", lambda: ScheduleLedger(dsn).ensure_schema())

    # studio.mcp audit store (dsn-keyword constructor)
    from studio.mcp.audit import PgToolAuditLog

    step("studio.mcp.audit (mcp_tool_audit)", lambda: PgToolAuditLog(dsn=dsn).ensure_schema())

    # customers extended columns (lazy ALTERs) — run them now, not mid-request
    from studio.customer_research import ensure_lead_columns

    step("customers ext columns", lambda: ensure_lead_columns(dsn))

    print(f"bootstrapped {len(ok)} stores: {', '.join(ok)}")
    if failed:
        print("FAILED:")
        for name, err in failed:
            print(f"  - {name}: {err}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
