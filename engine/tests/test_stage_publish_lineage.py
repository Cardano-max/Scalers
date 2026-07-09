"""stage_publish lineage fix (engine-core item 5, real PG).

The supervisor's approval-gated ``stage_publish`` tool used to stage drafts with NO
run_id and a random-uuid idempotency key — they sorted LAST in the review queue as
"Unassigned". It now derives a real camp_/team- run id, records a minimal
agent_runs + runs trail (lineage), and keys the action deterministically
(``{run_id}:{sha1(target|draft)[:12]}``). The HOLD posture is unchanged: the row is
PENDING, nothing sends.
"""

from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("ENGINE_DATABASE_URL"),
    reason="requires Postgres (set ENGINE_DATABASE_URL)",
)

DSN = os.environ.get("ENGINE_DATABASE_URL", "postgresql://scalers:scalers@localhost:5432/scalers")


def _run_tool(tenant: str, session: str, channel: str, draft: str, target: str) -> str:
    """Drive the tool body exactly as pydantic-ai would after approval."""
    import asyncio
    from types import SimpleNamespace

    from studio.agui import StudioDeps, stage_publish

    deps = StudioDeps(session_id=session, tenant_id=tenant, dsn=DSN)
    ctx = SimpleNamespace(deps=deps)
    return asyncio.run(stage_publish(ctx, channel, draft, target))


def test_stage_publish_stages_with_real_run_id_and_lineage():
    import psycopg

    tenant = "test_stagepub_" + uuid.uuid4().hex[:8]
    try:
        out = _run_tool(
            tenant, "sess-sp", "instagram",
            "Fresh fine-line work from the studio — book a consult.", "@studio",
        )
        assert "STAGED (held)" in out and "PENDING approval" in out
        with psycopg.connect(DSN, autocommit=True) as c:
            row = c.execute(
                "SELECT id, run_id, idempotency_key, status FROM actions "
                "WHERE tenant_id=%s ORDER BY created_at DESC LIMIT 1",
                (tenant,),
            ).fetchone()
        assert row is not None
        action_id, run_id, idem, status = row
        # REAL run id in the launch format — never NULL/"Unassigned".
        assert run_id and run_id.startswith("team-camp_")
        assert run_id in out
        # Deterministic idempotency key: run_id + target/draft hash.
        assert idem.startswith(f"{run_id}:") and len(idem.split(":")[-1]) == 12
        assert status == "pending"  # HELD, approve-first — nothing sent

        # Minimal lineage exists: one draft agent_run + a runs row for this run_id.
        with psycopg.connect(DSN, autocommit=True) as c:
            ar = c.execute(
                "SELECT role, model, output FROM agent_runs WHERE run_id=%s", (run_id,)
            ).fetchall()
            runs_row = c.execute(
                "SELECT run_id FROM runs WHERE run_id=%s", (run_id,)
            ).fetchone()
        assert len(ar) == 1 and ar[0][0] == "draft"
        assert ar[0][2]["source"] == "stage_publish"
        assert runs_row is not None

        # Cleanup lineage rows.
        with psycopg.connect(DSN, autocommit=True) as c:
            c.execute("DELETE FROM agent_runs WHERE run_id=%s", (run_id,))
            c.execute("DELETE FROM runs WHERE run_id=%s", (run_id,))
    finally:
        import psycopg

        with psycopg.connect(DSN, autocommit=True) as c:
            c.execute("DELETE FROM actions WHERE tenant_id=%s", (tenant,))


def test_stage_publish_still_refuses_foreign_identity():
    tenant = "test_stagepub_" + uuid.uuid4().hex[:8]
    out = _run_tool(
        tenant, "sess-sp2", "instagram",
        "hey it's Rae from Ladies First — come see us", "@x",
    )
    assert out.startswith("REFUSED")
    import psycopg

    with psycopg.connect(DSN, autocommit=True) as c:
        n = c.execute(
            "SELECT count(*) FROM actions WHERE tenant_id=%s", (tenant,)
        ).fetchone()[0]
    assert n == 0  # nothing written on refusal
