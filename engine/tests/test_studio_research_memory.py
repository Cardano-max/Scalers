"""Studio grounded-host: customer research + persistent memory + per-lead drafts.

Runs against the REAL local Postgres (the 60 seeded ``ladies8391`` customers +
personas). Uses the DETERMINISTIC embedder (no model download) so the memory
write/recall mechanics are exercised hermetically. Proves:

* ``MemoryStore.write``/``recall`` round-trip + idempotency (the persistent layer);
* ``customer_research.lookup_lead`` returns grounded facts for a seeded churn lead;
* ``build_outreach_draft`` is GROUNDED (uses real facts) and consent-aware;
* ``ingest_leads`` is idempotent on already-seeded leads (no duplicates);
* ``_research_and_stage_sync`` stages a PER-LEAD PENDING draft + writes a memory,
  and writes ZERO ``status='sent'`` rows (the HELD gate).
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager

import psycopg
import pytest

from kb.embedding import DeterministicEmbedder
from memory import MemoryStore
from studio.customer_research import (
    build_outreach_draft,
    choose_channel,
    ingest_leads,
    lookup_lead,
)

DSN = "postgresql://scalers:scalers@localhost:5432/scalers"
# READ-ONLY seeded ground truth (the 60 ladies8391 customers + personas). Tests
# may look these up but must NEVER write under this tenant — every write goes to
# a throwaway tenant that is deleted in ``finally`` (wwy.9: the live memories/KB
# are persistent client state, not a test scratchpad).
TENANT = "ladies8391"
# A seeded churn-risk lead (recon): Nadia Patel.
SEED_EMAIL = "nadia.patel59@fastmail.com"
SEED_NAME = "Nadia Patel"


def _require_db() -> None:
    try:
        with psycopg.connect(DSN, connect_timeout=3) as conn:
            conn.execute("SELECT 1")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"local Postgres not reachable ({exc})", allow_module_level=True)


_require_db()


def _det_store() -> MemoryStore:
    return MemoryStore(dsn=DSN, embedder=DeterministicEmbedder())


@contextmanager
def _throwaway_tenant():
    """A unique tenant id for WRITE tests; every row it accumulated (memories,
    staged actions, ingested customers) is deleted on exit so a suite run leaves
    the shared live DB byte-identical."""
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
                except psycopg.errors.UndefinedTable:  # table not provisioned here
                    pass


# ── persistent memory layer ───────────────────────────────────────────────── #


def test_memory_write_recall_roundtrip_and_idempotent() -> None:
    store = _det_store()
    store.ensure_schema()
    with _throwaway_tenant() as tenant:
        cust_id = "test_mem_" + uuid.uuid4().hex[:10]
        text = f"Prefers DM over email; asked about bridal flash twice ({cust_id})."
        mid1 = store.write(
            tenant_id=tenant, subject_type="customer", subject_id=cust_id,
            text=text, metadata={"kind": "preference"},
        )
        # Idempotent on the natural key — same text+subject returns the SAME row id.
        mid2 = store.write(
            tenant_id=tenant, subject_type="customer", subject_id=cust_id,
            text=text, metadata={"kind": "preference"},
        )
        assert mid1 == mid2

        # Recall with the exact text → identical vectors → cosine ~1.0 (works with the
        # non-semantic deterministic embedder too, so this stays hermetic).
        hits = store.recall(
            tenant_id=tenant, query=text,
            subject_type="customer", subject_id=cust_id, k=3,
        )
        assert any(h.text == text for h in hits)
        assert all(-1.0001 <= (h.similarity or 0.0) <= 1.0001 for h in hits)
        top = max(hits, key=lambda h: h.similarity or -2)
        assert top.text == text and (top.similarity or 0) > 0.99


def test_memory_rejects_bad_subject_type() -> None:
    store = _det_store()
    with pytest.raises(ValueError):
        store.write(tenant_id=TENANT, subject_type="tenant", subject_id=None, text="x")


# ── grounded customer research ─────────────────────────────────────────────── #


def test_lookup_lead_returns_grounded_facts_by_email() -> None:
    facts = lookup_lead(TENANT, email=SEED_EMAIL, dsn=DSN, memory_store=_det_store())
    assert facts is not None
    assert facts["name"] == SEED_NAME
    assert facts["customer_id"].startswith("cust_")
    assert isinstance(facts["interests"], list) and facts["interests"]
    assert "persona_traits" in facts and isinstance(facts["persona_traits"], dict)


def test_lookup_lead_honest_miss() -> None:
    assert lookup_lead(TENANT, email="nobody@nowhere.invalid", dsn=DSN) is None


def test_build_outreach_draft_is_grounded(monkeypatch) -> None:
    # Exercise the deterministic grounding/consent mechanics hermetically (no model):
    # the REAL copywriter-cell path is covered separately by the live smoke.
    monkeypatch.setenv("SCALERS_OUTREACH_LLM", "0")
    facts = lookup_lead(TENANT, email=SEED_EMAIL, dsn=DSN)
    draft = build_outreach_draft(facts, goal="win back lapsed clients")
    # Personalized: first name appears; grounding lists only real DB facts.
    assert SEED_NAME.split()[0] in draft["draft"]
    assert draft["grounding"] and any("name=" in g for g in draft["grounding"])
    assert draft["customer_id"] == facts["customer_id"]
    # No fabricated channel — must be one of the real action channels.
    assert draft["channel"] in ("gmail", "instagram", "facebook", "sms")


def test_build_outreach_draft_does_not_fabricate_recipient(monkeypatch) -> None:
    # Deterministic path: a brand-new studio lead with only name + city + CSV note
    # must produce honest copy and a grounding audit referencing only known facts.
    monkeypatch.setenv("SCALERS_OUTREACH_LLM", "0")
    facts = {
        "customer_id": "cust_smoke_unit",
        "name": "World Tattoo Studio",
        "email": "worldtattoostudio@example.invalid",
        "email_opt_in": True,
        "city": "Denver",
        "notes": "Primary contact for studio walk-ins/appts",
        "persona_traits": {},
        "interests": [],
        "tattoo_history": [],
    }
    draft = build_outreach_draft(facts, goal="introduce our studio", channel="gmail")
    assert draft["channel"] == "gmail"
    assert draft["target"] == facts["email"]
    assert (draft["draft"] or "").strip()
    assert any(g == "name=World Tattoo Studio" for g in draft["grounding"])
    assert any(g.startswith("city=") for g in draft["grounding"])


def test_choose_channel_respects_consent() -> None:
    # email best-channel but NOT opted in → must not pick email.
    facts = {
        "persona_traits": {"likely_best_channel": "email"},
        "preferred_channels": ["email"],
        "email_opt_in": False, "sms_opt_in": False,
    }
    assert choose_channel(facts, None) == "instagram"
    facts["email_opt_in"] = True
    assert choose_channel(facts, None) == "gmail"


def test_ingest_leads_idempotent_create_then_match() -> None:
    # Same upsert-by-(tenant,email) path that keeps seeded leads duplicate-free,
    # exercised against a THROWAWAY tenant so the run leaves zero residue in the
    # live customers table.
    with _throwaway_tenant() as tenant:
        rows = [{"name": "Ida Idempotent", "email": f"{tenant}@example.invalid",
                 "location": "Brooklyn, NY", "interests": "floral;color"}]
        first = ingest_leads(tenant, rows, dsn=DSN)
        assert first["created"] == 1
        again = ingest_leads(tenant, rows, dsn=DSN)
        assert again["created"] == 0  # already present → matched, never duplicated
        assert again["matched"] == 1
        assert again["customer_ids"] == first["customer_ids"]


# ── per-lead PENDING drafts + memory, HELD gate ────────────────────────────── #


def test_research_and_stage_writes_pending_drafts_and_memory(monkeypatch) -> None:
    monkeypatch.setenv("SCALERS_EMBEDDER", "deterministic")
    from studio.agui import CampaignPlan, _research_and_stage_sync

    with _throwaway_tenant() as tenant:
        # Provision the tenant's own lead first — the stage path researches leads
        # WITHIN the tenant, and writes must never land under ladies8391.
        email = f"lead-{tenant}@example.invalid"
        ingest_leads(
            tenant,
            [{"name": "Wanda Winback", "email": email, "location": "Brooklyn, NY",
              "interests": "floral;color", "notes": "lapsed since spring"}],
            dsn=DSN,
        )
        sid = "test-stage-" + uuid.uuid4().hex[:10]
        plan = CampaignPlan(goal="win back lapsed clients", channels=["instagram"])
        summary = _research_and_stage_sync(
            plan, sid, tenant, DSN, emails=[email], limit=10
        )
        assert summary["n_drafts"] == 1
        action_id = summary["staged"][0]["action_id"]

        with psycopg.connect(DSN, autocommit=True) as conn:
            # The staged draft is PENDING — never sent.
            row = conn.execute(
                "SELECT status, type, channel, draft FROM actions WHERE id = %s",
                (action_id,),
            ).fetchone()
            assert row is not None
            assert row[0] == "pending"
            assert row[1] == "outreach"
            assert (row[3] or "").strip()  # a real, non-empty draft body
            # ZERO sent rows for this session's staged action(s).
            sent = conn.execute(
                "SELECT count(*) FROM actions WHERE id = %s AND status = 'sent'",
                (action_id,),
            ).fetchone()[0]
            assert sent == 0

        # A memory of the outreach was persisted and is recallable on a LATER turn.
        cust_id = summary["staged"][0]["customer_id"]
        mems = _det_store().list_for_subject(
            tenant_id=tenant, subject_type="customer", subject_id=cust_id
        )
        assert any(m.metadata.get("session_id") == sid for m in mems)


# ── nmh.2: Review-Queue reliability — all drafts land + real lineage ─────────── #
# Regression for CustomerAcq-nmh.2: the research/stage path keyed idempotency on
# ``studio:{session}:{cust}:outreach`` — no run/goal discriminator — so a SECOND
# staging in the same session for the same customer (e.g. a different campaign
# goal) collided on the key and was SILENTLY dropped by ON CONFLICT DO NOTHING,
# and every staged row carried run_id=NULL (no campaign/run/evidence deep-link).


def _pending_for_customer(tenant: str, cust_id: str) -> list[dict]:
    with psycopg.connect(DSN, autocommit=True) as conn:
        rows = conn.execute(
            "SELECT id, run_id, subject FROM actions "
            "WHERE tenant_id=%s AND status='pending' "
            "AND idempotency_key LIKE %s ORDER BY created_at",
            (tenant, f"%{cust_id}%"),
        ).fetchall()
    return [{"id": r[0], "run_id": r[1], "subject": r[2]} for r in rows]


def test_research_stage_distinct_goals_both_land_with_run_id(monkeypatch) -> None:
    """Two stagings in ONE session for the SAME customer under DIFFERENT goals must
    BOTH land as distinct pending rows (no silent collision-drop), and each row must
    carry a real run_id so its campaign/run/evidence deep-links resolve (§15)."""
    monkeypatch.setenv("SCALERS_EMBEDDER", "deterministic")
    from studio.agui import CampaignPlan, _research_and_stage_sync

    with _throwaway_tenant() as tenant:
        email = f"lead-{tenant}@example.invalid"
        ingest_leads(
            tenant,
            [{"name": "Rex Return", "email": email, "location": "Queens, NY",
              "interests": "blackwork", "notes": "lapsed"}],
            dsn=DSN,
        )
        sid = "test-stage-" + uuid.uuid4().hex[:10]

        s1 = _research_and_stage_sync(
            CampaignPlan(goal="win back lapsed clients", channels=["instagram"]),
            sid, tenant, DSN, emails=[email], limit=10,
        )
        s2 = _research_and_stage_sync(
            CampaignPlan(goal="holiday flash promo", channels=["instagram"]),
            sid, tenant, DSN, emails=[email], limit=10,
        )
        assert s1["n_drafts"] == 1 and s2["n_drafts"] == 1
        cust_id = s1["staged"][0]["customer_id"]

        rows = _pending_for_customer(tenant, cust_id)
        # BOTH distinct-goal stagings landed — the second was NOT dropped.
        assert len(rows) == 2, f"expected 2 landed drafts, got {len(rows)}"
        # Every staged row carries a real run_id (non-null) so evidence deep-links work.
        assert all(r["run_id"] for r in rows), f"null run_id on: {rows}"
        # The two runs are distinct (different campaign intent → different run_id).
        assert rows[0]["run_id"] != rows[1]["run_id"]


def test_research_stage_same_batch_is_idempotent_no_double_stage(monkeypatch) -> None:
    """Re-driving the SAME request (same session, emails, goal) must be idempotent —
    the run_id is batch-stable, so a crash-then-re-drive re-stages ZERO duplicates
    (exactly-once STAGING; nothing is ever sent here regardless)."""
    monkeypatch.setenv("SCALERS_EMBEDDER", "deterministic")
    from studio.agui import CampaignPlan, _research_and_stage_sync

    with _throwaway_tenant() as tenant:
        email = f"lead-{tenant}@example.invalid"
        ingest_leads(
            tenant,
            [{"name": "Ida Idem", "email": email, "location": "Bronx, NY",
              "interests": "fineline", "notes": "lapsed"}],
            dsn=DSN,
        )
        sid = "test-stage-" + uuid.uuid4().hex[:10]
        plan = CampaignPlan(goal="win back lapsed clients", channels=["instagram"])

        _research_and_stage_sync(plan, sid, tenant, DSN, emails=[email], limit=10)
        _research_and_stage_sync(plan, sid, tenant, DSN, emails=[email], limit=10)

        cust_id = lookup_lead(tenant, email=email, dsn=DSN)["customer_id"]
        rows = _pending_for_customer(tenant, cust_id)
        assert len(rows) == 1, f"identical re-drive must not double-stage, got {len(rows)}"


def test_research_stage_dup_lead_counts_once(monkeypatch) -> None:
    """A lead repeated in one batch resolves to ONE action row (ON CONFLICT); the
    reported n_drafts must equal rows-in-queue, not the raw loop count (nmh.2 honesty:
    'exactly N appear')."""
    monkeypatch.setenv("SCALERS_EMBEDDER", "deterministic")
    from studio.agui import CampaignPlan, _research_and_stage_sync

    with _throwaway_tenant() as tenant:
        email = f"lead-{tenant}@example.invalid"
        ingest_leads(
            tenant,
            [{"name": "Uno Once", "email": email, "location": "Harlem, NY",
              "interests": "script", "notes": "lapsed"}],
            dsn=DSN,
        )
        sid = "test-stage-" + uuid.uuid4().hex[:10]
        # SAME email twice in the batch.
        summary = _research_and_stage_sync(
            CampaignPlan(goal="win back", channels=["instagram"]),
            sid, tenant, DSN, emails=[email, email], limit=10,
        )
        cust_id = lookup_lead(tenant, email=email, dsn=DSN)["customer_id"]
        rows = _pending_for_customer(tenant, cust_id)
        assert len(rows) == 1, f"dup lead must stage one row, got {len(rows)}"
        # reported count matches what actually landed — no over-count.
        assert summary["n_drafts"] == 1, f"n_drafts over-counted: {summary['n_drafts']}"
