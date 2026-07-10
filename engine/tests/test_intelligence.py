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


@pytest.mark.integration
@_pg
def test_conversation_rec_wording_matches_the_evidence():
    """Truth-gap fix 6: 'N verbatim conversation(s) on file carry real objections'
    overclaimed — the query only counted lead_conversations. The wording must match
    what is actually known: conversations ON FILE, and separately how many of those
    leads carry an analyst-CLASSIFIED objection (real linkage via the analyst step's
    customer_id + its run's tenant)."""
    import json
    import uuid as _uuid

    import psycopg

    from studio.conversations import upsert_conversation
    from studio.intelligence import campaign_intelligence

    dsn = os.environ["ENGINE_DATABASE_URL"]
    tenant = "t_intel_conv_" + uuid.uuid4().hex[:8]
    rid = "run_intel_" + _uuid.uuid4().hex[:8]
    upsert_conversation(
        tenant, "cust_a",
        [{"speaker": "customer", "text": "hey, thinking about a sleeve"}],
        dsn=dsn,
    )
    try:
        # Conversations on file, NOTHING classified yet: the rec must NOT claim the
        # conversations "carry real objections" — it points at the classify pass.
        out = campaign_intelligence(tenant, dsn=dsn)
        rec = next(r for r in out["recommendations"] if "Reactivation pass" in r["recommend"])
        assert "carry real objections" not in rec["why"]
        assert "run the reactivation pass to classify each" in rec["why"]
        assert rec["evidence"] == {
            "lead_conversations": 1,
            "leads_with_classified_objection": 0,
        }

        # Now a REAL analyst classification for that lead (tenant-scoped via its run).
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute(
                "INSERT INTO runs (run_id, tenant_id, type, trigger, status) "
                "VALUES (%s, %s, 'campaign', 'test', 'completed')",
                (rid, tenant),
            )
            conn.execute(
                "INSERT INTO agent_runs (id, campaign_id, run_id, role, model, input, "
                "output) VALUES (%s, %s, %s, 'analyst', 'test', %s::jsonb, %s::jsonb)",
                (
                    "agr_" + _uuid.uuid4().hex[:16], rid, rid,
                    json.dumps({"customer_id": "cust_a"}),
                    json.dumps({"primary_objection": "payment"}),
                ),
            )
        out2 = campaign_intelligence(tenant, dsn=dsn)
        rec2 = next(r for r in out2["recommendations"] if "Reactivation pass" in r["recommend"])
        assert "1 of those leads already carry an analyst-classified objection" in rec2["why"]
        assert rec2["evidence"]["leads_with_classified_objection"] == 1
    finally:
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute("DELETE FROM agent_runs WHERE run_id=%s", (rid,))
            conn.execute("DELETE FROM runs WHERE run_id=%s", (rid,))
            conn.execute("DELETE FROM lead_conversations WHERE tenant_id=%s", (tenant,))
