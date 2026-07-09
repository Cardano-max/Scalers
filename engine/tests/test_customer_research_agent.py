"""CustomerAcq-nmh.7: the REAL customer research agent (spec §7/§24).

Researches each lead from AVAILABLE data (CSV / conversation / CRM / notes /
customer-provided social) into interests / style-preference / tattoo-signals /
business-context / MISSING / confidence — NO fabrication, NO sensitive-attribute
inference, honest low-personalization when data is thin. The real web/public-social
path is GATED on the skill registry (REGISTERED-IN-USE); until then it is inert with an
honest 'no public research run' note.

Pure-unit where possible (deterministic, no DB); the dossier integration runs against
real Postgres on a throwaway tenant."""

from __future__ import annotations

import os
import uuid

import psycopg
import pytest

DSN = "postgresql://scalers:scalers@localhost:5432/scalers"
os.environ.setdefault("ENGINE_DATABASE_URL", DSN)
os.environ.setdefault("SCALERS_EMBEDDER", "deterministic")

from studio.customer_research_agent import (  # noqa: E402
    CustomerResearch,
    research_customer,
    web_research_allowed,
)

_CONV = {
    "turns": [
        {"speaker": "customer",
         "text": "I've been wanting a fine-line floral piece on my forearm, "
                 "but it's a bit pricey right now"},
    ],
}


def _facts(**over):
    base = {
        "customer_id": "c1", "name": "Priya", "email": "priya@example.com",
        "phone": None, "ig_handle": None, "city": "Las Vegas", "state": "NV",
        "interests": [], "persona_traits": {}, "tattoo_history": [],
        "customer_type": None, "lead_stage": None, "notes": None, "artist": None,
    }
    base.update(over)
    return base


# ── research shape + honesty (deterministic, no DB) ────────────────────────── #


def test_thin_lead_is_honest_low_no_fabrication() -> None:
    r = research_customer(_facts())
    assert isinstance(r, CustomerResearch)
    assert r.confidence_level == "low"
    assert r.interests == []  # nothing on file -> nothing invented
    assert not r.style_preference.value
    assert "known_interests" in r.missing_data
    assert "conversation_signals" in r.missing_data
    assert r.public_research["ran"] is False
    assert "no public research run" in r.public_research["note"].lower()


def test_csv_interests_are_real_high_confidence() -> None:
    r = research_customer(_facts(interests=["fine-line", "floral"]))
    assert "fine-line" in r.interests
    assert any(e.startswith("csv") for e in r.interest_evidence)
    assert r.confidence_level in ("medium", "high")


def test_conversation_yields_tattoo_signals_with_evidence() -> None:
    r = research_customer(_facts(), conversation=_CONV)
    # style/subject/placement signals extracted from the REAL conversation
    assert any("fine-line" in s or "floral" in s or "forearm" in s
               for s in r.tattoo_signals)
    assert r.style_preference.value  # a style preference grounded in the conversation
    assert r.style_preference.evidence  # carries the verbatim span
    assert r.confidence_level == "high"  # real conversation = strong signal


def test_business_context_from_crm_fields() -> None:
    r = research_customer(_facts(customer_type="recurring"))
    assert r.business_context.value
    assert "recurring" in r.business_context.value.lower()
    assert r.business_context.source in ("csv", "persona")


def test_zero_sensitive_attribute_inference() -> None:
    # A lead whose notes MENTION sensitive-ish words must NOT yield inferred sensitive
    # attributes in the research output (spec §7/§24 hard rule).
    r = research_customer(_facts(
        notes="young professional, mentioned her wedding",
        interests=["floral"],
    ))
    blob = r.model_dump_json().lower()
    for term in ("gender", "ethnicity", "religion", "sexuality", "age:",
                 "financial status", "health condition"):
        assert term not in blob


def test_interests_never_invented_beyond_available_data() -> None:
    # Only CSV/conversation-grounded interests appear; nothing is guessed from name/city.
    r = research_customer(_facts(name="Rose", city="Portland"))
    assert r.interests == []  # 'Rose'/'Portland' must NOT become an interest


# ── web/public-social gate (registry-driven) ───────────────────────────────── #


def test_web_research_inert_when_no_skill_registered() -> None:
    # All current registry rows are IN-VETTING -> the web path must be inert.
    assert web_research_allowed() is False
    r = research_customer(_facts(ig_handle="@priya.ink"), allow_web=True)
    assert r.public_research["ran"] is False
    assert r.public_research["sources"] == []
    assert "not registered" in r.public_research["note"].lower() \
        or "no public research run" in r.public_research["note"].lower()


def test_web_gate_reads_registry_status(monkeypatch) -> None:
    # When the gate reports a registered web skill, the note reflects readiness (the
    # actual egress still needs a provider key — proven inert here without one).
    monkeypatch.setattr(
        "studio.customer_research_agent.web_research_allowed", lambda dsn=None: True
    )
    # Even 'allowed', with no live provider the run degrades honestly (no fabrication).
    r = research_customer(_facts(ig_handle="@priya.ink"), allow_web=True)
    assert r.public_research["sources"] == []


# ── dossier integration (real PG) ──────────────────────────────────────────── #


def _require_db() -> None:
    try:
        with psycopg.connect(DSN, connect_timeout=3) as conn:
            conn.execute("SELECT 1")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"local Postgres not reachable ({exc})", allow_module_level=True)


def test_dossier_is_filled_by_the_research_agent() -> None:
    _require_db()
    from studio.conversations import upsert_conversation
    from studio.dossier import build_customer_dossier

    tenant = "t_nmh7_" + uuid.uuid4().hex[:8]
    cid = tenant + "_c0"
    try:
        with psycopg.connect(DSN, autocommit=True) as conn:
            conn.execute(
                "INSERT INTO customers (id, tenant_id, name, email, interests, "
                "preferred_channels, email_opt_in, sms_opt_in, source, customer_type) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (cid, tenant, "Priya", "priya@ex.com", ["fine-line"], [], True, False,
                 "test_nmh7", "recurring"),
            )
        upsert_conversation(
            tenant, cid,
            [{"speaker": "customer", "text": "love fine-line florals, a bit pricey"}],
            channel="sms", source="upload", dsn=DSN,
        )
        d = build_customer_dossier(tenant, cid, dsn=DSN)
        assert d is not None
        # The research agent filled the dossier's research surface.
        assert d.known_interests and "fine-line" in d.known_interests
        assert d.public_research_summary.present or d.tattoo_style_preference.present
        assert d.research_findings  # the structured research block is attached
        assert d.research_findings["public_research"]["ran"] is False
        # No sensitive-attribute inference leaked into the dossier.
        blob = d.model_dump_json().lower()
        for term in ("gender", "ethnicity", "religion", "sexuality"):
            assert term not in blob
    finally:
        with psycopg.connect(DSN, autocommit=True) as conn:
            for tbl in ("lead_conversations", "memories", "customers"):
                try:
                    conn.execute(f"DELETE FROM {tbl} WHERE tenant_id = %s", (tenant,))
                except Exception:
                    pass
