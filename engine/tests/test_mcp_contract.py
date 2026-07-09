"""MCP tool contract — hardening unit tests (DB-free).

Pins the security contract of :mod:`studio.mcp` without a database: tool-list
shape, access control (tenant binding + scope), input validation (malformed /
oversized / malicious rejected), honest not-connected errors, the protocol-vs-
execution error split, output sanitization, per-call auditing, and timeouts.

The DB-backed behaviors (real seeded rows + store-level cross-tenant isolation +
persisted Postgres audit) are proven in ``test_mcp_contract_integration.py``.
"""

from __future__ import annotations

import re

import pytest

from studio.mcp import (
    InMemoryAuditLog,
    McpToolServer,
    Principal,
    ProtocolError,
    ToolDef,
    UnknownToolError,
    args_hash,
    build_default_server,
    default_tools,
    demo_principal,
)
from studio.mcp.ratelimit import NullRateLimiter, SlidingWindowRateLimiter
from studio.mcp.sanitize import MAX_FIELD_LEN, sanitize_output
from studio.mcp.validation import MAX_ARGS_BYTES, validate_arguments

TENANT = "ladies8391"
_CSV = (
    "name,email,styles,artist,customer_type,notes\n"
    "Sarah Kim,sarah.kim@example.com,fine-line; floral,Maya,artist_specific,short on budget\n"
    "Priya Anand,priya.anand@example.com,script,Rae,artist_specific,first tattoo\n"
)
# All seven tool names, with a minimal *valid* argument set for each (used to
# prove access control fires before validation / any read, on every tool).
_ALL_TOOLS = [t.name for t in default_tools()]


@pytest.fixture
def server_and_audit():
    audit = InMemoryAuditLog()
    return build_default_server(audit=audit), audit


# ── tools/list shape ─────────────────────────────────────────────────────────
def test_list_tools_shape_and_names():
    srv = build_default_server()
    tools = srv.list_tools()["tools"]
    assert len(tools) == 7
    names = [t["name"] for t in tools]
    assert names == _ALL_TOOLS
    assert len(set(names)) == len(names)  # unique
    name_re = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")  # spec tool-name charset
    for t in tools:
        assert name_re.match(t["name"]), t["name"]
        assert t["description"]
        schema = t["inputSchema"]
        assert schema["type"] == "object"  # MUST be a valid JSON Schema object
        assert t["annotations"]["readOnlyHint"] is True
        assert t["annotations"]["destructiveHint"] is False


def test_list_tools_respects_principal_scope():
    srv = build_default_server()
    scoped = Principal.create("agent", TENANT, ["offers.list_offers"])
    listed = [t["name"] for t in srv.list_tools(scoped)["tools"]]
    assert listed == ["offers.list_offers"]  # least-privilege discovery


# ── valid calls (DB-free sources) ────────────────────────────────────────────
def test_valid_csv_leads_call_returns_real_rows(server_and_audit):
    srv, audit = server_and_audit
    p = demo_principal(TENANT)
    res = srv.call_tool(p, "crm.list_leads", {"source": "csv", "content": _CSV})
    assert res["isError"] is False
    body = res["structuredContent"]
    assert body["tenant_id"] == TENANT
    assert body["count"] == 2
    assert body["leads"][0]["name"] == "Sarah Kim"
    assert body["leads"][0]["artist"] == "Maya"
    # exactly one audit row, status ok, args recorded as a hash (not raw CSV).
    rows = audit.all()
    assert len(rows) == 1 and rows[0].status == "ok" and rows[0].tool == "crm.list_leads"
    assert _CSV not in rows[0].args_hash and len(rows[0].args_hash) == 64


def test_valid_seeded_artists_call(server_and_audit):
    srv, _ = server_and_audit
    res = srv.call_tool(demo_principal(TENANT), "artist.list_artists", {"source": "seeded"})
    assert res["isError"] is False
    assert {a["name"] for a in res["structuredContent"]["artists"]} == {"Maya", "Rae", "Noor"}


def test_valid_uploaded_conversation_call(server_and_audit):
    srv, _ = server_and_audit
    res = srv.call_tool(
        demo_principal(TENANT),
        "conversation.get_thread",
        {"source": "upload", "customer_id": "c1",
         "content": "Customer: I love the floral flash / Studio: want to book?"},
    )
    assert res["isError"] is False
    body = res["structuredContent"]
    assert body["found"] is True
    assert body["thread"]["turns"][0]["speaker"] == "customer"


def test_upload_conversation_unparseable_is_honest_not_found(server_and_audit):
    srv, _ = server_and_audit
    res = srv.call_tool(
        demo_principal(TENANT),
        "conversation.get_thread",
        {"source": "upload", "customer_id": "c1", "content": "no speakers here"},
    )
    assert res["isError"] is False
    assert res["structuredContent"]["found"] is False
    assert res["structuredContent"]["thread"] is None  # never fabricated


# ── access control: tenant binding ───────────────────────────────────────────
@pytest.mark.parametrize("tool", _ALL_TOOLS)
def test_cross_tenant_call_blocked_on_every_tool(tool, server_and_audit):
    """A call carrying a foreign tenant_id is refused BEFORE any read, on all
    seven tools (access control precedes validation + execution)."""
    srv, audit = server_and_audit
    p = demo_principal(TENANT)  # bound to ladies8391
    res = srv.call_tool(p, tool, {"tenant_id": "rival9999"})
    assert res["isError"] is True
    assert res["structuredContent"]["error"]["status"] == "access_denied"
    # audited as a denied attempt, under the principal's real tenant.
    row = audit.all()[-1]
    assert row.status == "access_denied" and row.tenant_id == TENANT and row.tool == tool


def test_scope_least_privilege_denied(server_and_audit):
    srv, _ = server_and_audit
    scoped = Principal.create("agent", TENANT, ["offers.list_offers"])
    res = srv.call_tool(scoped, "crm.list_leads", {"source": "csv", "content": _CSV})
    assert res["isError"] is True
    assert res["structuredContent"]["error"]["status"] == "access_denied"


def test_scoped_principal_allowed_its_own_tool(server_and_audit):
    srv, _ = server_and_audit
    scoped = Principal.create("agent", TENANT, ["crm.list_leads"])
    res = srv.call_tool(scoped, "crm.list_leads", {"source": "csv", "content": _CSV})
    assert res["isError"] is False and res["structuredContent"]["count"] == 2


# ── input validation: malformed / oversized / malicious rejected ─────────────
@pytest.mark.parametrize(
    "args",
    [
        {"source": "evil"},                       # enum violation
        {"source": "csv", "limit": 9999},          # int > maximum
        {"source": "csv", "limit": True},          # bool smuggled as int
        {"source": "csv", "content": 123},         # wrong type
        {"source": "csv", "unknown_key": 1},       # additionalProperties: false
        {"source": "csv", "content": "a\x00b"},    # NUL byte
        {"source": "csv", "content": "x" * 600_000},  # over field maxLength
    ],
)
def test_malformed_input_rejected(args, server_and_audit):
    srv, audit = server_and_audit
    res = srv.call_tool(demo_principal(TENANT), "crm.list_leads", args)
    assert res["isError"] is True
    assert res["structuredContent"]["error"]["status"] == "invalid_input"
    assert audit.all()[-1].status == "invalid_input"


def test_missing_required_field_rejected(server_and_audit):
    srv, _ = server_and_audit
    res = srv.call_tool(demo_principal(TENANT), "conversation.get_thread", {"source": "upload"})
    assert res["isError"] is True
    assert res["structuredContent"]["error"]["status"] == "invalid_input"


def test_oversized_total_args_rejected_by_validator():
    schema = {"type": "object", "additionalProperties": True, "properties": {}}
    huge = {"blob": "a" * (MAX_ARGS_BYTES + 10)}
    with pytest.raises(Exception) as ei:
        validate_arguments(schema, huge)
    assert "too large" in str(ei.value)


def test_control_char_customer_id_rejected(server_and_audit):
    srv, _ = server_and_audit
    res = srv.call_tool(
        demo_principal(TENANT),
        "conversation.get_thread",
        {"source": "db", "customer_id": "c\x001"},  # NUL in an id
    )
    assert res["isError"] is True
    assert res["structuredContent"]["error"]["status"] == "invalid_input"


# ── honest not-connected sources ─────────────────────────────────────────────
@pytest.mark.parametrize(
    "tool,args,needle",
    [
        ("crm.list_leads", {"source": "stribe"}, "Stribe is not connected"),
        ("crm.list_leads", {"source": "miniapp"}, "Mini-App CRM is not connected"),
        ("conversation.get_thread", {"source": "stribe", "customer_id": "c1"}, "Stribe SMS"),
        ("conversation.get_thread", {"source": "miniapp", "customer_id": "c1"}, "Mini-App CRM notes"),
        ("artist.list_artists", {"source": "miniapp"}, "Mini-App artist API"),
    ],
)
def test_not_connected_sources_surface_honest_error(tool, args, needle, server_and_audit):
    srv, audit = server_and_audit
    res = srv.call_tool(demo_principal(TENANT), tool, args)
    assert res["isError"] is True
    err = res["structuredContent"]["error"]
    assert err["status"] == "not_connected"
    assert needle in err["message"]  # the adapter's real message, not fabricated
    assert audit.all()[-1].status == "not_connected"


# ── protocol vs execution error split ────────────────────────────────────────
def test_unknown_tool_raises_protocol_error(server_and_audit):
    srv, audit = server_and_audit
    with pytest.raises(ProtocolError) as ei:
        srv.call_tool(demo_principal(TENANT), "does.not.exist", {})
    assert isinstance(ei.value, UnknownToolError)
    assert ei.value.code == -32601
    # still audited (finally runs even on a raised protocol error).
    assert audit.all()[-1].status == "protocol_error"


# ── output sanitization ──────────────────────────────────────────────────────
def test_sanitize_strips_ctrl_truncates_redacts_and_serializes():
    import datetime
    import json

    dirty = {
        "text": "hi\x00\x07there" + "z" * (MAX_FIELD_LEN + 50),
        "api_token": "sk-secret-123",
        "when": datetime.datetime(2026, 7, 1, 12, 0, 0),
        "nested": {"list": [1, 2, {"deep": "ok"}]},
    }
    out = sanitize_output(dirty)
    assert "\x00" not in out["text"] and "\x07" not in out["text"]
    assert out["text"].endswith("…[truncated]")
    assert out["api_token"] == "[redacted]"
    assert out["when"] == "2026-07-01T12:00:00"
    json.dumps(out)  # must be JSON-serializable


# ── timeout ──────────────────────────────────────────────────────────────────
def test_tool_timeout_is_reported_as_error():
    import time

    def _slow(principal, args, ctx):
        time.sleep(0.5)
        return {"ok": True}

    slow = ToolDef(
        name="slow.tool", title="Slow", description="sleeps past the deadline",
        input_schema={"type": "object", "additionalProperties": False, "properties": {}, "required": []},
        handler=_slow,
    )
    audit = InMemoryAuditLog()
    srv = McpToolServer([slow], audit=audit, timeout_s=0.05)
    res = srv.call_tool(demo_principal(TENANT), "slow.tool", {})
    assert res["isError"] is True
    assert res["structuredContent"]["error"]["status"] == "timeout"
    assert audit.all()[-1].status == "timeout"


# ── rate limiting ────────────────────────────────────────────────────────────
def test_rate_limit_caps_calls_per_principal():
    """Over the per-principal cap, the call is refused (rate_limited) and audited;
    every attempt — including the refused ones — is recorded."""
    audit = InMemoryAuditLog()
    srv = McpToolServer(
        default_tools(), audit=audit,
        rate_limiter=SlidingWindowRateLimiter(2, 60.0),  # 2 calls / window
    )
    p = demo_principal(TENANT)
    ok1 = srv.call_tool(p, "artist.list_artists", {"source": "seeded"})
    ok2 = srv.call_tool(p, "artist.list_artists", {"source": "seeded"})
    blocked = srv.call_tool(p, "artist.list_artists", {"source": "seeded"})
    assert ok1["isError"] is False and ok2["isError"] is False
    assert blocked["isError"] is True
    assert blocked["structuredContent"]["error"]["status"] == "rate_limited"
    assert audit.all()[-1].status == "rate_limited"
    assert [r.status for r in audit.all()] == ["ok", "ok", "rate_limited"]


def test_rate_limit_is_per_principal_not_global():
    """One principal hitting its cap must not block a different principal/tenant."""
    srv = McpToolServer(
        default_tools(), rate_limiter=SlidingWindowRateLimiter(1, 60.0)
    )
    a = demo_principal("tenant_a", subject="agent_a")
    b = demo_principal("tenant_b", subject="agent_b")
    assert srv.call_tool(a, "artist.list_artists", {"source": "seeded"})["isError"] is False
    assert srv.call_tool(a, "artist.list_artists", {"source": "seeded"})["isError"] is True
    # b has its own budget.
    assert srv.call_tool(b, "artist.list_artists", {"source": "seeded"})["isError"] is False


def test_null_rate_limiter_never_blocks():
    srv = McpToolServer(default_tools(), rate_limiter=NullRateLimiter())
    p = demo_principal(TENANT)
    for _ in range(50):
        assert srv.call_tool(p, "artist.list_artists", {"source": "seeded"})["isError"] is False


# ── audit hashing ────────────────────────────────────────────────────────────
def test_args_hash_is_order_independent_and_hex():
    h1 = args_hash({"a": 1, "b": 2})
    h2 = args_hash({"b": 2, "a": 1})
    assert h1 == h2 and len(h1) == 64 and re.match(r"^[0-9a-f]{64}$", h1)


def test_internal_handler_error_is_honest_generic(server_and_audit):
    """A handler that blows up returns an honest generic error (no leak), audited."""
    def _boom(principal, args, ctx):
        raise RuntimeError("secret internal detail")

    boom = ToolDef(
        name="boom.tool", title="Boom", description="raises",
        input_schema={"type": "object", "additionalProperties": False, "properties": {}, "required": []},
        handler=_boom,
    )
    audit = InMemoryAuditLog()
    srv = McpToolServer([boom], audit=audit)
    res = srv.call_tool(demo_principal(TENANT), "boom.tool", {})
    assert res["isError"] is True
    assert res["structuredContent"]["error"]["status"] == "error"
    assert "secret internal detail" not in res["structuredContent"]["error"]["message"]
    assert audit.all()[-1].status == "error" and audit.all()[-1].error_kind == "RuntimeError"
