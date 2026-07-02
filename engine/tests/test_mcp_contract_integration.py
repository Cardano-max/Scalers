"""MCP tool contract — integration proof against a REAL Postgres + seeded data.

Marked ``integration`` (skipped when no DB). Proves the behaviors that a DB-free
unit test cannot:

  * a valid DB-backed call returns REAL seeded rows (conversation turns, offers,
    documents),
  * STORE-LEVEL tenant isolation: because a DB handler reads with the principal's
    own tenant, a customer / offer / document that exists under tenant A is
    invisible to a principal bound to tenant B (this is *below* the access-control
    gate — even with no cross-tenant argument, the read cannot leak),
  * the offer-substantiation no-fabrication gate over MCP (real code succeeds, an
    invented code fails closed),
  * the Postgres audit backend persists one row per call, every outcome.

Two ephemeral tenants (unique per run) are seeded so the real ``ladies8391``
tenant is untouched and the isolation assertions are unambiguous.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from studio.mcp import (
    McpToolServer,
    Principal,
    PgToolAuditLog,
    build_default_server,
    default_tools,
    demo_principal,
)

pytestmark = pytest.mark.integration

DB_DSN = "postgresql://scalers:scalers@localhost:5432/scalers"


def _require_db() -> None:
    try:
        with psycopg.connect(DB_DSN, connect_timeout=3) as conn:
            conn.execute("SELECT 1")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"local Postgres not reachable at {DB_DSN} ({exc})",
                    allow_module_level=True)


_require_db()


@pytest.fixture(scope="module")
def seeded():
    """Seed two isolated tenants (A, B) + a bare tenant (C) with real seed logic.

    Returns their ids and A's seeded customer ids so tests can address real rows."""
    from studio.documents import add_document, seed_tenant_documents
    from studio.seed_tattoo_leads import seed_warm_leads

    run = uuid.uuid4().hex[:8]
    tenant_a = f"mcp_it_a_{run}"
    tenant_b = f"mcp_it_b_{run}"
    tenant_c = f"mcp_it_c_{run}"  # deliberately unseeded → honest-empty reads

    a = seed_warm_leads(tenant_a, dsn=DB_DSN)
    seed_tenant_documents(tenant_a, dsn=DB_DSN)
    b = seed_warm_leads(tenant_b, dsn=DB_DSN)

    # A private, uniquely-named asset in tenant A — used to prove the assets
    # library is tenant-scoped (B must never see it).
    marker = f"SECRET-A-{run}"
    add_document(tenant_a, marker, f"# {marker}\nprivate to tenant A only.",
                 kind="brand", source="test", dsn=DB_DSN)

    return {
        "a": tenant_a, "b": tenant_b, "c": tenant_c,
        "a_customer_ids": a["customer_ids"],
        "b_customer_ids": b["customer_ids"],
        "marker": marker,
    }


@pytest.fixture
def server():
    return build_default_server(dsn=DB_DSN)


# ── real seeded rows ─────────────────────────────────────────────────────────
def test_db_conversation_returns_real_seeded_turns(server, seeded):
    p = demo_principal(seeded["a"])
    cid = seeded["a_customer_ids"][0]
    res = server.call_tool(p, "conversation.get_thread", {"source": "db", "customer_id": cid})
    assert res["isError"] is False
    body = res["structuredContent"]
    assert body["found"] is True
    assert len(body["thread"]["turns"]) >= 1
    assert body["thread"]["turns"][0]["speaker"] in ("customer", "studio")


def test_offers_list_returns_real_seeded_offers(server, seeded):
    res = server.call_tool(demo_principal(seeded["a"]), "offers.list_offers", {})
    assert res["isError"] is False
    codes = {o["code"] for o in res["structuredContent"]["offers"]}
    assert "FLOWER15" in codes  # the real seeded offer
    assert res["structuredContent"]["count"] == 5


def test_documents_list_and_retrieve_real(server, seeded):
    p = demo_principal(seeded["a"])
    docs = server.call_tool(p, "assets.list_documents", {})
    assert docs["isError"] is False
    names = {d["name"] for d in docs["structuredContent"]["documents"]}
    assert seeded["marker"] in names
    # full-text retrieve returns real passages for a query that matches the doc.
    hit = server.call_tool(p, "assets.retrieve", {"query": seeded["marker"]})
    assert hit["isError"] is False
    assert hit["structuredContent"]["count"] >= 1


# ── store-level tenant isolation (below the access gate) ─────────────────────
def test_conversation_store_isolation(server, seeded):
    """A's customer is visible to A but INVISIBLE to B — even though B passes no
    cross-tenant argument. The read is scoped to the principal's tenant."""
    a_cid = seeded["a_customer_ids"][0]
    seen_by_a = server.call_tool(
        demo_principal(seeded["a"]), "conversation.get_thread",
        {"source": "db", "customer_id": a_cid},
    )
    seen_by_b = server.call_tool(
        demo_principal(seeded["b"]), "conversation.get_thread",
        {"source": "db", "customer_id": a_cid},
    )
    assert seen_by_a["structuredContent"]["found"] is True
    assert seen_by_b["structuredContent"]["found"] is False  # no cross-tenant leak


def test_assets_library_isolation(server, seeded):
    """A's private document must not appear in B's or the bare tenant C's list."""
    a_docs = server.call_tool(demo_principal(seeded["a"]), "assets.list_documents", {})
    b_docs = server.call_tool(demo_principal(seeded["b"]), "assets.list_documents", {})
    c_docs = server.call_tool(demo_principal(seeded["c"]), "assets.list_documents", {})
    a_names = {d["name"] for d in a_docs["structuredContent"]["documents"]}
    b_names = {d["name"] for d in b_docs["structuredContent"]["documents"]}
    assert seeded["marker"] in a_names
    assert seeded["marker"] not in b_names
    assert c_docs["structuredContent"]["count"] == 0  # unseeded → honest empty


def test_bare_tenant_offers_honest_empty(server, seeded):
    res = server.call_tool(demo_principal(seeded["c"]), "offers.list_offers", {})
    assert res["isError"] is False
    assert res["structuredContent"]["count"] == 0  # never a fabricated discount


# ── substantiation gate over MCP ─────────────────────────────────────────────
def test_substantiate_real_code_and_rejects_fake(server, seeded):
    # 65w.14 posture (CustomerAcq-ju1.2): a SEED/MOCK offers doc never substantiates a
    # live draft — only a REAL operator-provided offers doc does. The seeded FLOWER15
    # (source='seed') is now correctly REFUSED; a real-doc code substantiates.
    from studio.documents import add_document
    from studio.offers import OFFERS_DOC_KIND

    p = demo_principal(seeded["a"])
    # The seeded MOCK offer is refused — seed sources never substantiate.
    seed = server.call_tool(p, "offers.substantiate", {"code": "FLOWER15"})
    assert seed["structuredContent"]["substantiated"] is False
    assert seed["structuredContent"]["offer"] is None
    # A REAL operator-provided offers doc DOES substantiate its code.
    add_document(
        seeded["a"], "Real Offers", "- code: REALINK20 | discount: 20% | kind: discount",
        kind=OFFERS_DOC_KIND, source="operator",
        doc_id=f"doc_offers_{seeded['a']}_real", dsn=DB_DSN,
    )
    real = server.call_tool(p, "offers.substantiate", {"code": "REALINK20"})
    assert real["structuredContent"]["substantiated"] is True
    assert real["structuredContent"]["offer"]["code"] == "REALINK20"
    # A fully-invented code is refused (no fabrication).
    fake = server.call_tool(p, "offers.substantiate", {"code": "TOTALLY-INVENTED-99"})
    assert fake["structuredContent"]["substantiated"] is False
    assert fake["structuredContent"]["offer"] is None


# ── Postgres audit persistence ───────────────────────────────────────────────
def test_pg_audit_persists_every_outcome(seeded):
    audit = PgToolAuditLog(DB_DSN)
    srv = McpToolServer(default_tools(), audit=audit, dsn=DB_DSN)
    p = demo_principal(seeded["a"])
    srv.call_tool(p, "offers.list_offers", {})                       # ok
    srv.call_tool(p, "offers.list_offers", {"tenant_id": "rival"})    # access_denied
    srv.call_tool(p, "crm.list_leads", {"source": "stribe"})          # not_connected

    rows = audit.list_rows(tenant_id=seeded["a"], limit=100)
    statuses = {r["status"] for r in rows}
    assert {"ok", "access_denied", "not_connected"} <= statuses
    # args recorded as a 64-hex sha256, never raw.
    assert all(len(r["args_hash"]) == 64 for r in rows)
    assert all(r["tenant_id"] == seeded["a"] for r in rows)
