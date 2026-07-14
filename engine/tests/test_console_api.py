"""ju1.5 console read API — campaign-example memory + draft lineage endpoints.

Lane 1 (DB-free): screenshot resolution honesty + traversal guard.
Lane 2 (integration, real PG): the three endpoints end-to-end — examples list with
screenshot URLs, screenshot streaming from the LOCAL client-data dir, and draft
lineage assembled from the staged dossier + customers/artists rows, with the
honest-missing contract (absent fields are None, never fabricated).
"""

from __future__ import annotations

import json
import os
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from studio.console_api import _resolve_screenshot, mount_console_api

DSN = os.environ.get("ENGINE_DATABASE_URL", "postgresql://scalers:scalers@localhost:5432/scalers")

integration = pytest.mark.skipif(
    not os.getenv("ENGINE_DATABASE_URL"),
    reason="requires Postgres (set ENGINE_DATABASE_URL)",
)


def _client() -> TestClient:
    app = FastAPI()
    mount_console_api(app)
    return TestClient(app)


# ── lane 1: screenshot resolution (DB-free) ───────────────────────────────────


def test_resolve_screenshot_finds_extensionless_slack_id(tmp_path, monkeypatch):
    monkeypatch.setenv("SCALERS_CLIENT_DATA_DIR", str(tmp_path))
    (tmp_path / "screenshots").mkdir()
    (tmp_path / "screenshots" / "F0TEST123.png").write_bytes(b"\x89PNG fake")
    assert _resolve_screenshot("F0TEST123").name == "F0TEST123.png"
    assert _resolve_screenshot("F0TEST123.png").name == "F0TEST123.png"


def test_resolve_screenshot_honest_none_when_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("SCALERS_CLIENT_DATA_DIR", str(tmp_path))
    (tmp_path / "screenshots").mkdir()
    assert _resolve_screenshot("NOPE") is None
    assert _resolve_screenshot(None) is None
    assert _resolve_screenshot("") is None


def test_resolve_screenshot_strips_path_components(tmp_path, monkeypatch):
    """A stored value can never escape the screenshots dir — only its basename is
    used, so '../../secret.png' resolves inside screenshots/ (and misses)."""
    monkeypatch.setenv("SCALERS_CLIENT_DATA_DIR", str(tmp_path))
    (tmp_path / "screenshots").mkdir()
    (tmp_path / "secret.png").write_bytes(b"outside")
    assert _resolve_screenshot("../secret.png") is None
    assert _resolve_screenshot("../../secret") is None


# ── lane 2: endpoints on real PG ──────────────────────────────────────────────


@integration
def test_campaign_examples_endpoint_lists_examples_and_patterns(tmp_path, monkeypatch):
    from studio.campaign_examples_store import import_campaign_examples

    tenant = f"ju15t_{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv("SCALERS_CLIENT_DATA_DIR", str(tmp_path))
    (tmp_path / "screenshots").mkdir()
    (tmp_path / "screenshots" / "SHOT_A.png").write_bytes(b"\x89PNG fake")

    payload = {
        "_provenance": {"source": "test synthetic", "extraction": "synthetic"},
        "campaigns": [
            {"source_screenshot": "SHOT_A.png", "campaign_name": "07.01 Maya Flash $800",
             "status": "Sent", "artist_name": "Maya", "offer_price_usd": 800,
             "recipient_count": 200, "delivered_count": 150, "failed_count": 20,
             "dnd_blocked_count": 30, "message_copy": "MAYA SPECIAL — reply YES",
             "cta": "reply YES"},
            {"source_screenshot": "MISSING_SHOT.png", "campaign_name": "07.02 Bella $500",
             "status": "Sent", "artist_name": "Bella", "offer_price_usd": 500,
             "recipient_count": 100, "delivered_count": 90, "message_copy": "BELLA",
             "cta": "reply BELLA"},
        ],
    }
    src = tmp_path / "examples.json"
    src.write_text(json.dumps(payload), encoding="utf-8")
    import_campaign_examples(src, tenant, dsn=DSN)

    try:
        res = _client().get("/studio/campaign-examples", params={"tenant_id": tenant})
        assert res.status_code == 200
        body = res.json()
        assert body["tenantId"] == tenant
        by_name = {e["campaign_name"]: e for e in body["examples"]}
        assert set(by_name) == {"07.01 Maya Flash $800", "07.02 Bella $500"}
        maya = by_name["07.01 Maya Flash $800"]
        assert maya["offer_price_usd"] == 800.0          # Decimal -> JSON-safe float
        assert maya["screenshot_url"].endswith("/screenshot")
        # honest-missing: the file for Bella's screenshot is absent locally
        assert by_name["07.02 Bella $500"]["screenshot_url"] is None
        assert isinstance(body["patterns"], list)

        # unknown tenant reads honestly empty, never an error or fabrication
        empty = _client().get("/studio/campaign-examples",
                              params={"tenant_id": "t_never"}).json()
        assert empty["examples"] == [] and empty["patterns"] == []

        # screenshot endpoint streams the real local file for Maya...
        shot = _client().get(maya["screenshot_url"])
        assert shot.status_code == 200
        assert shot.headers["content-type"] == "image/png"
        assert shot.content == b"\x89PNG fake"
        # ...404s for Bella (row exists, file absent) and for an unknown example
        assert _client().get(by_name["07.02 Bella $500"]["id"].join(
            ["/studio/campaign-examples/", "/screenshot"])).status_code == 404
        assert _client().get("/studio/campaign-examples/cex_nope/screenshot").status_code == 404
    finally:
        import psycopg

        with psycopg.connect(DSN, autocommit=True) as conn:
            conn.execute("DELETE FROM campaign_example_patterns WHERE tenant_id = %s", (tenant,))
            conn.execute("DELETE FROM campaign_examples WHERE tenant_id = %s", (tenant,))


@integration
def test_action_lineage_assembles_dossier_customer_and_studio():
    import psycopg

    from actions.store import ensure_schema, record_pending_action
    from studio.client_import import _ARTISTS_DDL, _CUSTOMER_EXT_DDL
    from studio.customer_research import upsert_lead

    tenant = f"ju15t_{uuid.uuid4().hex[:8]}"
    run_id = f"run_{uuid.uuid4().hex[:10]}"
    ensure_schema(DSN)
    with psycopg.connect(DSN, autocommit=True) as conn:
        for ddl in _CUSTOMER_EXT_DDL:
            conn.execute(ddl)
        conn.execute(_ARTISTS_DDL)

    lead = upsert_lead(tenant, {"name": "Kai Client", "email": "kai@example.com"}, dsn=DSN)
    cust_id = lead["customer_id"]
    art_id = f"art_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(
            "UPDATE customers SET source_file = %s, artist = %s, phone = %s WHERE id = %s",
            ("customers.csv", "Maya", "+1-555-0100", cust_id),
        )
        conn.execute(
            "INSERT INTO artists (id, tenant_id, name) VALUES (%s, %s, %s)"
            " ON CONFLICT (id) DO NOTHING",
            (art_id, tenant, "Maya"),
        )
        conn.execute(
            "INSERT INTO artist_studios (artist_id, studio_name) VALUES (%s, %s)"
            " ON CONFLICT DO NOTHING",
            (art_id, "Skin Design Tattoo Hawaii"),
        )

    context = json.dumps({
        "skill_used": "sk1", "dossier": {
            "customer_id": cust_id, "run_id": run_id,
            "name": {"value": "Kai Client", "confidence": "high", "source": "db:customers.name"},
            "email": {"value": "kai@example.com", "confidence": "high", "source": "db:customers.email"},
            "phone": {"value": "+1-555-0100", "confidence": "high", "source": "db:customers.phone"},
            "recommended_cta": {"value": "reply YES to book", "confidence": "medium",
                                "source": "goal+channel"},
            "best_angle": {"value": "flash day", "confidence": "medium",
                           "source": "objection+goal+offer:FLASH800"},
            "limited_personalization": True,
            "personalization_note": "no conversation history for this lead",
        },
    })
    action_id = record_pending_action(
        tenant_id=tenant, decision_id=None, type="outreach", channel="sms",
        worker="studio_provided_leads", target="+1-555-0100",
        draft="Flash day — reply YES", subject=None, context=context, conf=0.8,
        threshold=None, esc_kind="approval_required", esc_label="test",
        idempotency_key=f"{run_id}:{cust_id}", run_id=run_id, dsn=DSN,
    )

    try:
        res = _client().get(f"/studio/action/{action_id}/lineage")
        assert res.status_code == 200
        body = res.json()
        assert body["sourceFile"] == "customers.csv"
        assert body["customer"] == {"id": cust_id, "name": "Kai Client",
                                    "email": "kai@example.com", "phone": "+1-555-0100"}
        assert body["artist"] == "Maya"
        assert body["studio"] == "Skin Design Tattoo Hawaii"   # via artist_studios join
        assert body["offer"] == "FLASH800"                     # parsed from angle source
        assert body["cta"] == "reply YES to book"
        assert body["channel"] == "sms"
        assert body["examples"] == []                          # honest: ju1.4 not wired
        assert body["limitedPersonalization"] is True

        assert _client().get("/studio/action/act_nope/lineage").status_code == 404
    finally:
        import psycopg

        with psycopg.connect(DSN, autocommit=True) as conn:
            conn.execute("DELETE FROM actions WHERE tenant_id = %s", (tenant,))
            conn.execute("DELETE FROM artist_studios WHERE artist_id = %s", (art_id,))
            conn.execute("DELETE FROM artists WHERE tenant_id = %s", (tenant,))
            conn.execute("DELETE FROM customers WHERE tenant_id = %s", (tenant,))


@integration
def test_action_lineage_honest_missing_when_no_context():
    from actions.store import ensure_schema, record_pending_action

    tenant = f"ju15t_{uuid.uuid4().hex[:8]}"
    ensure_schema(DSN)
    action_id = record_pending_action(
        tenant_id=tenant, decision_id=None, type="outreach", channel="gmail",
        worker="w", target="x@example.com", draft="d", subject=None, context=None,
        conf=0.8, threshold=None, esc_kind="approval_required", esc_label="t",
        idempotency_key=f"nolineage:{uuid.uuid4().hex[:8]}", run_id=None, dsn=DSN,
    )
    try:
        body = _client().get(f"/studio/action/{action_id}/lineage").json()
        assert body["sourceFile"] is None and body["artist"] is None and body["studio"] is None
        assert body["customer"] == {"id": None, "name": None, "email": None, "phone": None}
        assert body["offer"] is None and body["cta"] is None
        assert body["channel"] == "gmail" and body["examples"] == []
    finally:
        import psycopg

        with psycopg.connect(DSN, autocommit=True) as conn:
            conn.execute("DELETE FROM actions WHERE tenant_id = %s", (tenant,))


@integration
def test_action_contributions_assembles_per_agent_trail():
    """The Agent Contributions endpoint reads the REAL agent_runs trail: per-lead
    cells matched on THIS draft's customer (never another lead's), run-level cells
    (strategist/jury) applied campaign-wide, identity counts surfaced, and the
    location entry honest-missing when nothing is on file."""
    import psycopg

    from actions.store import ensure_schema, record_pending_action
    from studio.client_import import _ARTISTS_DDL, _CUSTOMER_EXT_DDL
    from studio.customer_research import upsert_lead

    tenant = f"ju15t_{uuid.uuid4().hex[:8]}"
    run_id = f"run_{uuid.uuid4().hex[:10]}"
    ensure_schema(DSN)
    with psycopg.connect(DSN, autocommit=True) as conn:
        for ddl in _CUSTOMER_EXT_DDL:
            conn.execute(ddl)
        conn.execute(_ARTISTS_DDL)

    lead = upsert_lead(tenant, {"name": "Rio Lead", "email": "rio@example.com"}, dsn=DSN)
    cust_id = lead["customer_id"]
    other = upsert_lead(tenant, {"name": "Other Lead", "email": "other@example.com"}, dsn=DSN)

    rows = [
        ("strategist", "anthropic:test", {}, {"positioning": "Trusted studio, zero pressure"}),
        # researcher for ANOTHER lead first — the endpoint must skip it
        ("researcher", "firecrawl+customer_db", {"customer_id": other["customer_id"]},
         {"customer_id": other["customer_id"], "cited": 9, "public_enrichment": {"found": 9}}),
        ("researcher", "firecrawl+customer_db", {"customer_id": cust_id},
         {"customer_id": cust_id, "cited": 2,
          "sources": [{"url": "https://example.org/a"}, {"url": "https://example.org/b"}],
          "db_history": {"past_tattoos": 1, "lifecycle": "returning"},
          "public_enrichment": {"found": 2,
                                "identity": {"confirmed": 1, "likely": 1,
                                             "uncertain": 3, "rejected": 2},
                                "unverified_detail": [
                                    {"url": "https://about.me/riolead",
                                     "reason": "name-only match — could be any "
                                               "person with this name"}]}}),
        ("analyst", "anthropic:test", {"customer_id": cust_id},
         {"customer_id": cust_id, "primary_objection": "price", "objection_signal": "stated",
          "readiness_stage": "consideration", "grounded_fields": 8,
          "objection_evidence": ["that's more than I expected"]}),
        ("draft", "anthropic:test", {"customer_id": cust_id},
         {"customer_id": cust_id, "hook": "your sleeve, phased", "angle": "price"}),
        ("critic", "anthropic:test", {"customer_id": cust_id},
         {"customer_id": cust_id, "verdict": "approve", "confidence": 0.9, "rationale": "grounded"}),
        ("jury", "deterministic:gate", {}, {"note": "1 of 1 staged HELD", "decision": "review"}),
    ]
    with psycopg.connect(DSN, autocommit=True) as conn:
        for role, model, inp, out in rows:
            conn.execute(
                "INSERT INTO agent_runs (id, campaign_id, run_id, role, model, input, output)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (f"ar_{uuid.uuid4().hex[:10]}", "camp_t", run_id, role, model,
                 json.dumps(inp), json.dumps(out)),
            )

    context = json.dumps({"dossier": {"customer_id": cust_id, "run_id": run_id}})
    action_id = record_pending_action(
        tenant_id=tenant, decision_id=None, type="outreach", channel="gmail",
        worker="studio_provided_leads", target="rio@example.com", draft="d",
        subject=None, context=context, conf=0.8, threshold=None,
        esc_kind="approval_required", esc_label="test",
        idempotency_key=f"{run_id}:{cust_id}", run_id=run_id, dsn=DSN,
    )

    try:
        res = _client().get(f"/studio/action/{action_id}/contributions")
        assert res.status_code == 200
        body = res.json()
        assert body["runId"] == run_id and body["customerId"] == cust_id
        assert body["agentRunCount"] == len(rows)
        by_agent = {e["agent"]: e for e in body["contributions"]}

        assert by_agent["Strategy"]["output"] == "Trusted studio, zero pressure"
        # THIS lead's researcher cell, not the other lead's 9-cite cell
        assert by_agent["Research"]["output"] == "2 cited source(s)"
        assert by_agent["Research"]["evidence"] == [
            "https://example.org/a", "https://example.org/b"]
        assert by_agent["Research"]["dbHistory"] == {
            "past_tattoos": 1, "lifecycle": "returning"}
        assert "1 confirmed" in by_agent["Identity Guardian"]["output"]
        assert "3 uncertain" in by_agent["Identity Guardian"]["output"]
        # the set-aside candidate is SHOWN with its reason — never personalized on
        assert by_agent["Identity Guardian"]["evidence"] == [
            "set aside: https://about.me/riolead — name-only match — could be any "
            "person with this name"]
        # no city/location on file -> honest-missing, never invented
        assert by_agent["Location Resolver"]["status"] == "missing"
        assert "not invented" in by_agent["Location Resolver"]["output"]
        assert by_agent["Analyst"]["personalization"]["level"] == "high"
        assert "objection: price (stated)" in by_agent["Analyst"]["output"]
        assert by_agent["Copywriter"]["output"].startswith("hook: your sleeve")
        assert by_agent["Critic"]["output"] == "verdict: approve · confidence 0.9"
        assert by_agent["Jury"]["output"] == "1 of 1 staged HELD"

        assert _client().get("/studio/action/act_nope/contributions").status_code == 404
    finally:
        with psycopg.connect(DSN, autocommit=True) as conn:
            conn.execute("DELETE FROM actions WHERE tenant_id = %s", (tenant,))
            conn.execute("DELETE FROM agent_runs WHERE run_id = %s", (run_id,))
            conn.execute("DELETE FROM customers WHERE tenant_id = %s", (tenant,))
