"""Evidence-cited lead enrichment (studio.lead_enrichment).

Covers the four honesty contracts:

(a) DB-gated: a monkeypatched research seam returning fake CITED facts → ONE
    customer memory written with the URLs inline + in metadata; re-enriching
    REPLACES that memory (never stacks duplicates). Throwaway tenant, cleaned up.
(b) PURE: sensitive-trait facts ("appears to be in her 20s", "likely hispanic
    male", …) are suppressed by the post-filter and COUNTED — never stored.
(c) DB-gated: a zero-fact research result → NO memory write, ``{"found": []}``
    honest miss with per-query notes.
(d) No login-walled fabrication: a fact without a URL (or without verbatim text)
    is rejected by the storage path — pure gate test + the end-to-end DB proof.

The research seam (``studio.lead_enrichment.run_trend_research``, re-exported from
``studio.ig_pipeline``) is monkeypatched everywhere — no live egress in tests.
"""

from __future__ import annotations

import os
import uuid
from contextlib import contextmanager

import psycopg
import pytest

DSN = (
    os.environ.get("ENGINE_DATABASE_URL")
    or "postgresql://scalers:scalers@localhost:5432/scalers"
)


def _db_up() -> bool:
    try:
        with psycopg.connect(DSN, connect_timeout=3) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


requires_db = pytest.mark.skipif(
    not _db_up(), reason=f"local Postgres not reachable at {DSN}"
)


@contextmanager
def _throwaway_tenant():
    """A unique tenant for WRITE tests; every row it accumulated is deleted on
    exit so a suite run leaves the shared live DB byte-identical (wwy.9)."""
    tenant = "test_tenant_" + uuid.uuid4().hex[:10]
    try:
        yield tenant
    finally:
        with psycopg.connect(DSN, autocommit=True) as conn:
            for table in ("memories", "actions", "customers"):
                try:
                    conn.execute(
                        f"DELETE FROM {table} WHERE tenant_id = %s", (tenant,)
                    )
                except psycopg.errors.UndefinedTable:
                    pass


def _fake_seam(sources_by_call: list[list[dict]], notes: str = "no usable sources"):
    """A stand-in for the shared research seam with the SAME contract as
    ``run_trend_research``: {query, sources, cited, note}. Each successive call
    pops the next canned source list (repeating the last one when exhausted)."""
    calls = {"n": 0}

    def fake(query: str, *, limit: int = 5) -> dict:
        idx = min(calls["n"], len(sources_by_call) - 1)
        calls["n"] += 1
        sources = sources_by_call[idx]
        if not sources:
            return {"query": query, "sources": [], "cited": 0, "note": notes}
        return {"query": query, "sources": sources, "cited": len(sources), "note": None}

    return fake


def _ingest_lead(tenant: str, *, email: str, name: str = "Mia Chen") -> str:
    from studio.customer_research import ingest_leads

    res = ingest_leads(
        tenant,
        [{"name": name, "email": email, "location": "Las Vegas, NV",
          "interests": "watercolor painting"}],
        dsn=DSN,
    )
    return res["customer_ids"][0]


def _enrichment_memories(tenant: str, cust_id: str) -> list[dict]:
    with psycopg.connect(DSN, autocommit=True) as conn:
        rows = conn.execute(
            "SELECT id, text, metadata FROM memories WHERE tenant_id = %s "
            "AND subject_type = 'customer' AND subject_id = %s "
            "AND metadata->>'source' = 'public-web-enrichment' ORDER BY created_at",
            (tenant, cust_id),
        ).fetchall()
    return [{"id": r[0], "text": r[1], "metadata": r[2]} for r in rows]


# ── (a) cited facts → memory with URLs; re-run replaces, never stacks ───────── #


@requires_db
def test_enrich_writes_memory_with_urls_and_rerun_replaces(monkeypatch) -> None:
    monkeypatch.setenv("SCALERS_EMBEDDER", "deterministic")
    import studio.lead_enrichment as le

    with _throwaway_tenant() as tenant:
        cust_id = _ingest_lead(tenant, email=f"mia@{uuid.uuid4().hex[:8]}-bakes.com")

        first_hit = {
            "title": "Mia Chen — Mia's Bakery Las Vegas",
            "snippet": "Owner at Mia's Bakery. Custom cakes and watercolor-style "
                       "pastry art in Las Vegas.",
            "url": "https://miasbakery.example.com/about",
        }
        monkeypatch.setattr(le, "run_trend_research", _fake_seam([[first_hit]]))
        res = le.enrich_lead(tenant, cust_id, dsn=DSN)

        assert res["found"], "cited facts must survive to the result"
        assert all(f["url"] and f["quote"] for f in res["found"])
        assert res["suppressed"] == 0
        assert res["memory_id"]

        mems = _enrichment_memories(tenant, cust_id)
        assert len(mems) == 1, "exactly ONE enrichment memory"
        assert first_hit["url"] in mems[0]["text"], "URL must ride inline in the text"
        assert first_hit["url"] in mems[0]["metadata"]["urls"]
        assert mems[0]["metadata"]["source"] == "public-web-enrichment"
        assert mems[0]["metadata"]["enriched_at"], "enriched_at from DB now()"

        # Re-enrich with DIFFERENT public evidence: the previous enrichment memory
        # is REPLACED (same natural key: the source tag) — never stacked.
        second_hit = {
            "title": "Mia's Bakery wins Best of Vegas",
            "snippet": "Mia Chen's Las Vegas bakery took the 2026 pastry-art award.",
            "url": "https://vegasnews.example.com/best-of-vegas-2026",
        }
        monkeypatch.setattr(le, "run_trend_research", _fake_seam([[second_hit]]))
        res2 = le.enrich_lead(tenant, cust_id, dsn=DSN)
        assert res2["memory_id"]

        mems2 = _enrichment_memories(tenant, cust_id)
        assert len(mems2) == 1, f"re-enrich must replace, got {len(mems2)} memories"
        assert second_hit["url"] in mems2[0]["text"]
        assert first_hit["url"] not in mems2[0]["text"]

        # Identical re-run: still exactly one (idempotent on identical evidence).
        le.enrich_lead(tenant, cust_id, dsn=DSN)
        assert len(_enrichment_memories(tenant, cust_id)) == 1


# ── (b) sensitive-trait facts are suppressed + counted (pure) ────────────────── #


def test_sensitive_trait_facts_suppressed_and_counted() -> None:
    from studio.lead_enrichment import SENSITIVE_KEYS, suppress_sensitive

    sensitive = [
        {"quote": "appears to be in her 20s", "url": "https://x.example/a", "query": "q"},
        {"quote": "likely hispanic male", "url": "https://x.example/b", "query": "q"},
        {"quote": "a devout christian who attends church weekly",
         "url": "https://x.example/c", "query": "q"},
        {"quote": "recovering from surgery and in therapy",
         "url": "https://x.example/d", "query": "q"},
        {"quote": "openly gay and active in LGBTQ groups",
         "url": "https://x.example/e", "query": "q"},
        {"quote": "votes for republicans every cycle",
         "url": "https://x.example/f", "query": "q"},
        {"quote": "broke and living paycheck to paycheck",
         "url": "https://x.example/g", "query": "q"},
    ]
    clean = {
        "quote": "Owner at Mia's Bakery — custom cakes and pastry art in Las Vegas",
        "url": "https://miasbakery.example.com/about", "query": "q",
    }
    kept, suppressed = suppress_sensitive([*sensitive, clean])
    assert suppressed == len(sensitive), "every sensitive-trait fact is dropped"
    assert kept == [clean], "the clean professional fact survives untouched"
    # The registry covers the full banned-category set (no silent narrowing).
    assert len(SENSITIVE_KEYS) == 9


@requires_db
def test_sensitive_facts_never_reach_the_memory(monkeypatch) -> None:
    """End-to-end: a seam that returns ONLY sensitive-trait assertions yields a
    counted suppression, an honest miss result, and ZERO memory writes."""
    monkeypatch.setenv("SCALERS_EMBEDDER", "deterministic")
    import studio.lead_enrichment as le

    with _throwaway_tenant() as tenant:
        cust_id = _ingest_lead(tenant, email="mia@gmail.com")  # freemail → 2 queries
        bad = {
            "title": "profile roundup",
            "snippet": "she is probably in her 20s, likely hispanic",
            "url": "https://gossip.example.com/people",
        }
        monkeypatch.setattr(le, "run_trend_research", _fake_seam([[bad]]))
        res = le.enrich_lead(tenant, cust_id, dsn=DSN)
        assert res["found"] == []
        assert res["suppressed"] == 1
        assert res["memory_id"] is None
        assert _enrichment_memories(tenant, cust_id) == []


# ── (c) zero-fact result → honest miss, no memory write ─────────────────────── #


@requires_db
def test_zero_facts_is_an_honest_miss_no_write(monkeypatch) -> None:
    monkeypatch.setenv("SCALERS_EMBEDDER", "deterministic")
    import studio.lead_enrichment as le

    with _throwaway_tenant() as tenant:
        cust_id = _ingest_lead(tenant, email=f"mia@{uuid.uuid4().hex[:8]}-bakes.com")
        monkeypatch.setattr(
            le, "run_trend_research",
            _fake_seam([[]], notes="trend research unavailable (no key armed)"),
        )
        res = le.enrich_lead(tenant, cust_id, dsn=DSN)
        assert res["found"] == []
        assert res["memory_id"] is None
        assert res["suppressed"] == 0
        # Every query is reported as a miss with the seam's concrete note.
        assert res["misses"] and all(m["query"] and m["note"] for m in res["misses"])
        assert _enrichment_memories(tenant, cust_id) == []


def test_unknown_customer_raises_lookup_error() -> None:
    if not _db_up():
        pytest.skip("local Postgres not reachable")
    from studio.lead_enrichment import enrich_lead

    with pytest.raises(LookupError):
        enrich_lead("test_tenant_nobody", "cust_does_not_exist", dsn=DSN)


# ── (d) citation gate: a fact without a URL is rejected by the storage path ──── #


def test_storage_path_rejects_uncited_facts_pure() -> None:
    from studio.lead_enrichment import citable_facts

    facts = [
        {"quote": "Owner at Mia's Bakery", "url": "", "query": "q"},          # no URL
        {"quote": "Owner at Mia's Bakery", "url": None, "query": "q"},        # no URL
        {"quote": "", "url": "https://x.example/a", "query": "q"},            # no text
        {"quote": "Owner at Mia's Bakery",
         "url": "https://miasbakery.example.com/about", "query": "q"},        # cited
    ]
    kept = citable_facts(facts)
    assert kept == [facts[3]], "only the url+quote fact may be stored"


@requires_db
def test_uncited_seam_hits_never_write_a_memory(monkeypatch) -> None:
    """A provider hit with no URL (the login-walled-fabrication shape: detail with
    nothing citable behind it) is rejected end-to-end — not in ``found``, and no
    memory row is written."""
    monkeypatch.setenv("SCALERS_EMBEDDER", "deterministic")
    import studio.lead_enrichment as le

    with _throwaway_tenant() as tenant:
        cust_id = _ingest_lead(tenant, email=f"mia@{uuid.uuid4().hex[:8]}-bakes.com")
        uncited = {
            "title": "Mia Chen | LinkedIn",
            "snippet": "500+ connections. Bakery owner.",
            "url": None,  # nothing citable → must be rejected
        }
        monkeypatch.setattr(le, "run_trend_research", _fake_seam([[uncited]]))
        res = le.enrich_lead(tenant, cust_id, dsn=DSN)
        assert res["found"] == []
        assert res["memory_id"] is None
        assert _enrichment_memories(tenant, cust_id) == []


@requires_db
def test_login_walled_hit_is_stored_as_pointer_with_snippet(monkeypatch) -> None:
    """A login-walled host WITH a real URL + public snippet is kept — as a URL
    pointer carrying exactly what the public search snippet said, flagged so no
    downstream consumer mistakes it for a crawled profile."""
    monkeypatch.setenv("SCALERS_EMBEDDER", "deterministic")
    import studio.lead_enrichment as le

    with _throwaway_tenant() as tenant:
        cust_id = _ingest_lead(tenant, email=f"mia@{uuid.uuid4().hex[:8]}-bakes.com")
        walled = {
            "title": "Mia Chen — Owner, Mia's Bakery",
            "snippet": "Las Vegas pastry business owner.",
            "url": "https://www.linkedin.com/in/mia-chen-bakes",
        }
        monkeypatch.setattr(le, "run_trend_research", _fake_seam([[walled]]))
        res = le.enrich_lead(tenant, cust_id, dsn=DSN)
        assert len(res["found"]) >= 1
        fact = res["found"][0]
        assert fact["login_walled"] is True
        assert fact["url"] == walled["url"]
        mems = _enrichment_memories(tenant, cust_id)
        assert len(mems) == 1
        assert "login-walled profile" in mems[0]["text"]
        assert walled["url"] in mems[0]["text"]


# ── query shaping: business domain in, freemail out; nameless honest miss ────── #


def test_business_domain_shapes_a_query_freemail_does_not() -> None:
    from studio.lead_enrichment import build_enrichment_queries, business_email_domain

    assert business_email_domain("mia@gmail.com") is None
    assert business_email_domain("mia@example.invalid") is None
    assert business_email_domain("mia@miasbakery.com") == "miasbakery.com"

    biz = build_enrichment_queries(
        {"name": "Mia Chen", "city": "Las Vegas", "email": "mia@miasbakery.com",
         "interests": ["watercolor painting"]}
    )
    assert 1 <= len(biz) <= 3
    labels = [label for label, _q in biz]
    assert "business-domain" in labels
    assert any('"miasbakery.com"' in q for _l, q in biz)
    assert all('"Mia Chen"' in q for _l, q in biz), "name is always quoted"

    free = build_enrichment_queries(
        {"name": "Mia Chen", "city": "Las Vegas", "email": "mia@gmail.com",
         "interests": []}
    )
    assert [label for label, _q in free] == ["public-presence"]
    assert all("gmail.com" not in q for _l, q in free)

    assert build_enrichment_queries({"name": "", "email": "x@y.com"}) == []


# ── dossier surfacing: the enrichment memory feeds the copywriter prompt ─────── #


def test_enrichment_prompt_lines_from_facts_memories() -> None:
    from studio.lead_enrichment import enrichment_prompt_lines

    url = "https://miasbakery.example.com/about"
    facts = {
        "memories": [
            {"text": "Staged gmail outreach ...", "metadata": {"kind": "outreach"}},
            {"text": ("Public-web enrichment for Mia Chen — verifiable public facts:"
                      f'\n- "Owner at Mia\'s Bakery" ({url})'),
             "metadata": {"source": "public-web-enrichment", "urls": [url]}},
        ]
    }
    lines = enrichment_prompt_lines(facts)
    assert lines and "PUBLIC-WEB ENRICHMENT" in lines[1]
    assert any(url in ln for ln in lines), "facts surface WITH their URLs"

    # Honest-empty: no enrichment memory → no block at all.
    assert enrichment_prompt_lines({"memories": []}) == []
    assert enrichment_prompt_lines(None) == []


def test_email_prompt_carries_labeled_enrichment_block() -> None:
    from studio.customer_research import _build_email_prompt, _choose_angle

    url = "https://miasbakery.example.com/about"
    facts = {
        "customer_id": "cust_x", "name": "Mia Chen", "city": "Las Vegas",
        "email": "mia@miasbakery.com", "email_opt_in": True,
        "persona_traits": {}, "interests": [], "tattoo_history": [],
        "memories": [
            {"text": ("Public-web enrichment for Mia Chen — verifiable public facts:"
                      f'\n- "Owner at Mia\'s Bakery. Custom cakes." ({url})'),
             "metadata": {"source": "public-web-enrichment", "urls": [url]}},
        ],
    }
    angle = _choose_angle(facts, None)
    prompt = _build_email_prompt(facts, goal="say hello", research=[], angle=angle)
    assert "PUBLIC-WEB ENRICHMENT" in prompt
    assert url in prompt, "the draft prompt can cite the enrichment URL"

    # Without the enrichment memory the block is honestly absent.
    bare = dict(facts, memories=[])
    prompt2 = _build_email_prompt(bare, goal="say hello", research=[],
                                  angle=_choose_angle(bare, None))
    assert "PUBLIC-WEB ENRICHMENT" not in prompt2
