"""conversation_lead_index — the host's cohort-from-conversations receipt tool.

DB-gated (real lead_conversations + customers rows under a throwaway tenant):
the index must list only leads with an imported thread, filter by what the
CUSTOMER (not the studio) said, and return their words verbatim — this is the
seam that lets 'pick three who stepped back over price' resolve to real people.
"""

from __future__ import annotations

import json
import os
import uuid

import pytest

from studio.customer_research import conversation_lead_index

_pg = pytest.mark.skipif(
    not os.environ.get("ENGINE_DATABASE_URL"),
    reason="requires Postgres (set ENGINE_DATABASE_URL)",
)


@pytest.fixture()
def seeded_tenant():
    import psycopg

    dsn = os.environ["ENGINE_DATABASE_URL"]
    tenant = f"t_convidx_{uuid.uuid4().hex[:8]}"
    rows = [
        # (customer, [(speaker, text), ...])
        ("cust_a", "Ava Price", "ava@x.example", [
            ("studio", "Hi Ava! Ready to book your session?"),
            ("customer", "Honestly the price is a bit steep for me right now"),
        ]),
        ("cust_b", "Ben Busy", "ben@x.example", [
            ("customer", "Work got crazy, can we reschedule to next month?"),
        ]),
        ("cust_c", "Cara Clean", "cara@x.example", [
            ("customer", "That flash sheet is gorgeous, count me in!"),
        ]),
        # Price word said by the STUDIO, not the customer — must NOT match 'price'.
        ("cust_d", "Dan Quiet", "dan@x.example", [
            ("studio", "Our price list is attached"),
            ("customer", "Thanks, looks great"),
        ]),
    ]
    with psycopg.connect(dsn, autocommit=True) as conn:
        for cid, name, email, turns in rows:
            conn.execute(
                "INSERT INTO customers (id, tenant_id, name, email) "
                "VALUES (%s, %s, %s, %s)",
                (cid, tenant, name, email),
            )
            conn.execute(
                "INSERT INTO lead_conversations (id, tenant_id, customer_id, "
                "channel, source, turns) VALUES (%s, %s, %s, 'sms', 'test', %s)",
                (f"conv_{cid}", tenant, cid,
                 json.dumps([{"speaker": s, "text": t} for s, t in turns])),
            )
    yield tenant
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("DELETE FROM lead_conversations WHERE tenant_id=%s", (tenant,))
        conn.execute("DELETE FROM customers WHERE tenant_id=%s", (tenant,))


@_pg
def test_no_topic_lists_every_conversation_lead(seeded_tenant):
    out = conversation_lead_index(seeded_tenant)
    assert {r["name"] for r in out} == {"Ava Price", "Ben Busy", "Cara Clean", "Dan Quiet"}
    assert all(r["quote"] is None for r in out)
    assert all(r["turns"] >= 1 for r in out)


@_pg
def test_price_topic_matches_only_customer_words_verbatim(seeded_tenant):
    out = conversation_lead_index(seeded_tenant, topic="price")
    assert [r["name"] for r in out] == ["Ava Price"]
    # The quote is the customer's exact words — the receipt, never a paraphrase.
    assert out[0]["quote"] == "Honestly the price is a bit steep for me right now"


@_pg
def test_timing_topic_matches_reschedule_family(seeded_tenant):
    out = conversation_lead_index(seeded_tenant, topic="timing")
    assert [r["name"] for r in out] == ["Ben Busy"]
    assert "reschedule" in out[0]["quote"]


@_pg
def test_unknown_topic_is_literal_substring(seeded_tenant):
    out = conversation_lead_index(seeded_tenant, topic="flash sheet")
    assert [r["name"] for r in out] == ["Cara Clean"]


@_pg
def test_limit_caps_the_index(seeded_tenant):
    out = conversation_lead_index(seeded_tenant, limit=2)
    assert len(out) == 2
