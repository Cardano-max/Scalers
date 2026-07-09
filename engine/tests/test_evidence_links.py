"""Evidence route extension (engine-core item 7, real PG): artwork + artist +
customer-dossier links ride on GET /studio/action/{id}/evidence — additive keys,
each present ONLY when the underlying fact exists on the action's own context."""

from __future__ import annotations

import json
import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("ENGINE_DATABASE_URL"),
    reason="requires Postgres (set ENGINE_DATABASE_URL)",
)

DSN = os.environ.get("ENGINE_DATABASE_URL", "postgresql://scalers:scalers@localhost:5432/scalers")


def test_evidence_links_from_action_context(monkeypatch):
    import psycopg

    from actions.store import ensure_schema, record_pending_action
    from studio.agui import _evidence_links

    tenant = "test_evlinks_" + uuid.uuid4().hex[:8]
    run_id = f"team-camp_{uuid.uuid4().hex[:8]}-{uuid.uuid4().hex[:8]}"
    try:
        with psycopg.connect(DSN, autocommit=True) as c:
            c.execute(
                "INSERT INTO artists (id, tenant_id, name) VALUES (%s,%s,%s)",
                (f"art_test_{uuid.uuid4().hex[:12]}", tenant, "Bella"),
            )
        ensure_schema(DSN)
        ctx = {
            "artist": "Bella",
            "artwork": {"assetId": "asset1", "artifactId": "art_img_1",
                        "vlmSummary": "fine-line"},
            "attachment_artifact_id": "art_img_1",
            "dossier": {"customer_id": "cust_1", "name": {"value": "Nadia"}},
        }
        action_id = record_pending_action(
            tenant_id=tenant, decision_id=None, type="outreach", channel="gmail",
            worker="t", target="n@x.com", draft="d", context=json.dumps(ctx),
            conf=None, threshold=None, esc_kind="approval_required", esc_label="t",
            idempotency_key=f"{run_id}:c1", run_id=run_id, dsn=DSN,
        )
        links = _evidence_links(action_id, DSN)
        assert links["artwork"]["assetId"] == "asset1"
        assert links["artwork"]["rawUrl"] == "/studio/artifacts/art_img_1/raw"
        assert links["attachmentArtifactId"] == "art_img_1"
        assert links["artist"]["slug"] == "bella"
        assert links["artist"]["url"] == "/studio/artists/bella"
        assert links["customerDossier"]["customerId"] == "cust_1"
        assert "tenant_id=" in links["customerDossier"]["url"]

        # A bare action (no artwork/artist/dossier) adds NO fabricated links.
        bare_id = record_pending_action(
            tenant_id=tenant, decision_id=None, type="outreach", channel="gmail",
            worker="t", target="m@x.com", draft="d2", context=None,
            conf=None, threshold=None, esc_kind="approval_required", esc_label="t",
            idempotency_key=f"{run_id}:c2", run_id=run_id, dsn=DSN,
        )
        assert _evidence_links(bare_id, DSN) == {}
        assert _evidence_links("act_missing", DSN) == {}
    finally:
        with psycopg.connect(DSN, autocommit=True) as c:
            c.execute("DELETE FROM actions WHERE tenant_id=%s", (tenant,))
            c.execute("DELETE FROM artists WHERE tenant_id=%s", (tenant,))
