"""CustomerAcq-nmh.6 part 2: CUSTOMER DOSSIER (spec §8) — real fields + explicit
MISSING where data is absent; never fabricated depth.

Real-PG on a throwaway tenant. Proves the honesty contract: a thin lead (name/email
only) shows exactly that, lists what is missing, and is graded low personalization —
NOT a pretend-deep profile.
"""

from __future__ import annotations

import os
import uuid

import psycopg
import pytest

DSN = "postgresql://scalers:scalers@localhost:5432/scalers"
os.environ.setdefault("ENGINE_DATABASE_URL", DSN)
os.environ.setdefault("SCALERS_EMBEDDER", "deterministic")


def _require_db() -> None:
    try:
        with psycopg.connect(DSN, connect_timeout=3) as conn:
            conn.execute("SELECT 1")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"local Postgres not reachable ({exc})", allow_module_level=True)


_require_db()

from studio.conversations import upsert_conversation  # noqa: E402
from studio.dossier import build_customer_dossier  # noqa: E402


def _tenant() -> str:
    return "t_nmh6dos_" + uuid.uuid4().hex[:8]


def _teardown(tenant: str) -> None:
    with psycopg.connect(DSN, autocommit=True) as conn:
        for tbl in ("lead_conversations", "memories", "customer_personas", "customers"):
            try:
                conn.execute(f"DELETE FROM {tbl} WHERE tenant_id = %s", (tenant,))
            except Exception:
                pass


def _mk(tenant, cid, **cols):
    keys = ["id", "tenant_id", "interests", "preferred_channels", "email_opt_in",
            "sms_opt_in", "source"]
    vals = [cid, tenant, cols.pop("interests", []), [], True, False, "test_nmh6"]
    for k, v in cols.items():
        keys.append(k)
        vals.append(v)
    ph = ", ".join(["%s"] * len(keys))
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(
            f"INSERT INTO customers ({', '.join(keys)}) VALUES ({ph})", vals
        )


def test_thin_lead_shows_real_fields_and_explicit_missing() -> None:
    tenant, cid = _tenant(), "c_thin"
    try:
        _mk(tenant, cid, name="Sarah", email="sarah@example.com")
        d = build_customer_dossier(tenant, cid, dsn=DSN)
        # Real fields present
        assert d.name.present and d.name.value == "Sarah"
        assert d.email.present
        # Absent data is EXPLICITLY listed as missing, not fabricated
        assert "conversation_summary" in d.missing_data
        assert "social_handle" in d.missing_data
        assert "phone" in d.missing_data
        assert not d.conversation_summary.present
        # Honest low personalization (spec example: name/email only -> low)
        assert d.personalization_level == "low"
        assert d.limited_personalization is True
    finally:
        _teardown(tenant)


def test_rich_lead_has_fewer_missing_and_higher_personalization() -> None:
    tenant, cid = _tenant(), "c_rich"
    try:
        _mk(tenant, cid, name="Priya", email="priya@example.com",
            phone="+15550000001", ig_handle="@priya.ink",
            interests=["fine-line", "floral"], artist="Angel",
            city="Las Vegas", customer_type="recurring")
        upsert_conversation(
            tenant, cid,
            [{"speaker": "customer", "text": "love the fine-line florals, a bit pricey though"}],
            channel="sms", source="upload", dsn=DSN,
        )
        d = build_customer_dossier(tenant, cid, dsn=DSN)
        assert d.phone.present and d.social_handle.present
        assert d.conversation_summary.present
        assert d.known_interests  # real interests list populated
        assert "fine-line" in d.known_interests
        assert d.artist_style_match.present
        assert d.personalization_level in ("medium", "high")
        # Nothing core is falsely reported missing
        assert "name" not in d.missing_data
        assert "conversation_summary" not in d.missing_data
    finally:
        _teardown(tenant)


def test_missing_customer_returns_none() -> None:
    tenant = _tenant()
    try:
        assert build_customer_dossier(tenant, "does_not_exist", dsn=DSN) is None
    finally:
        _teardown(tenant)


def test_dossier_never_infers_sensitive_attributes() -> None:
    tenant, cid = _tenant(), "c_sens"
    try:
        _mk(tenant, cid, name="Alex", email="alex@example.com")
        d = build_customer_dossier(tenant, cid, dsn=DSN)
        blob = d.model_dump_json().lower()
        # No fabricated protected/sensitive traits (spec §7): gender/age/ethnicity/etc.
        for term in ("gender", "ethnicity", "religion", "sexuality", "age:"):
            assert term not in blob
    finally:
        _teardown(tenant)
