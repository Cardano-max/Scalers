"""Campaign Intelligence tenant isolation: a brand-new (empty) tenant must see
honest emptiness in EVERY section — never another client's patterns, artists,
or objection analytics (a QA empty-tenant probe caught exactly that leak)."""

from __future__ import annotations

import os
import uuid

import pytest

_pg = pytest.mark.skipif(
    not os.environ.get("ENGINE_DATABASE_URL"),
    reason="requires Postgres (set ENGINE_DATABASE_URL)",
)


@pytest.mark.integration
@_pg
def test_empty_tenant_sees_no_foreign_intelligence():
    from studio.intelligence import campaign_intelligence

    dsn = os.environ["ENGINE_DATABASE_URL"]
    out = campaign_intelligence("t_intel_empty_" + uuid.uuid4().hex[:8], dsn=dsn)
    assert out["bestCampaigns"] == []
    assert out["patterns"] == []       # leaked cross-tenant before the fix
    assert out["artists"] == []        # leaked cross-tenant before the fix
    assert out["objections"] == []     # leaked cross-tenant before the fix
    assert out["reviewQueue"] == []
    assert out["competitors"] == []
    recs = out["recommendations"]
    assert len(recs) == 1 and "Import campaign history" in recs[0]["recommend"]


@pytest.mark.integration
@_pg
def test_scoped_sections_match_direct_sql_for_live_tenant():
    """The scoping must not LOSE the real tenant's rows: every section equals a
    direct tenant-scoped SQL recount."""
    import psycopg

    from studio.intelligence import campaign_intelligence

    dsn = os.environ["ENGINE_DATABASE_URL"]
    out = campaign_intelligence("skindesign", dsn=dsn)
    with psycopg.connect(dsn, autocommit=True) as conn:
        want_patterns = conn.execute(
            "SELECT count(*) FROM campaign_example_patterns WHERE tenant_id='skindesign'"
        ).fetchone()[0]
        want_artists = conn.execute(
            "SELECT count(DISTINCT content->>'artist') FROM assets "
            "WHERE coalesce(content->>'artist','') <> '' "
            "AND campaign_id = 'portfolio:skindesign'"
        ).fetchone()[0]
        want_objs = conn.execute(
            "SELECT count(DISTINCT ar.output->>'primary_objection') FROM agent_runs ar "
            "JOIN runs r ON r.run_id = ar.run_id AND r.tenant_id='skindesign' "
            "WHERE ar.role='analyst' AND ar.output->>'primary_objection' IS NOT NULL "
            "AND ar.output->>'primary_objection' NOT IN ('', 'none-found')"
        ).fetchone()[0]
    assert len(out["patterns"]) == min(want_patterns, 8)
    assert len(out["artists"]) == min(want_artists, 8)
    assert len(out["objections"]) == min(want_objs, 8)
