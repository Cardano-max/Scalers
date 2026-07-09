"""nmh.4 acceptance — universal upload → supervisor + agent file access (real PG).

The bead's AC, end to end: upload a CSV + a brand-voice file + an image; the
VOICE supervisor answers "what files / how many images" correctly from real
state; and OTHER agents can access the parsed content in a run.

* The supervisor path is ``studio.inventory.build_data_inventory`` — the ONE
  shared builder both the chat host and the realtime voice supervisor read
  (voice.py ``_data_inventory_block``). We assert it now reports the real file
  counts by type + the image count.
* The agent path is the ``studio.artifacts`` store: ``build_artifacts_context``
  (injected into the host + run agents) and ``list_artifacts(include_content=True)``
  carry the parsed CSV / brand-voice content a cell grounds on in a run.

Runs on a private schema (wwy.9) so it never touches the live registry; the
image-route smoke uses a throwaway tenant with finally-cleanup.
"""

from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("ENGINE_DATABASE_URL"),
    reason="requires Postgres (set ENGINE_DATABASE_URL)",
)

DSN = os.environ.get("ENGINE_DATABASE_URL", "postgresql://scalers:scalers@localhost:5432/scalers")


def test_supervisor_and_agents_see_uploaded_files_from_real_state():
    from tests.conftest import private_schema
    from studio import artifacts
    from studio.inventory import build_data_inventory

    with private_schema("20-context-artifacts.sql") as s:
        # Upload a CSV + a brand-voice file + an image (what the three routes do).
        artifacts.register_artifact(
            "ladies8391",
            "customers.csv",
            "csv",
            media_type="text/csv",
            summary="CSV: 500 rows; columns: name, email, city",
            parsed_content="name,email,city\nNadia,nadia@x.com,Brooklyn",
            meta={"rows": 500},
            dsn=s.dsn,
        )
        artifacts.register_artifact(
            "ladies8391",
            "brand-voice.md",
            "brand_voice",
            media_type="text/markdown",
            summary="Ladies First brand voice — warm, direct",
            parsed_content="Tone: warm, direct. Ban: 'slay'. Lead with the work.",
            dsn=s.dsn,
        )
        artifacts.register_artifact(
            "ladies8391",
            "sleeve.png",
            "image",
            media_type="image/png",
            preview="data:image/png;base64,iVBORw0KGgo",
            meta={"bytes": 4096},
            dsn=s.dsn,
        )

        # SUPERVISOR: build_data_inventory (voice + chat share it) answers correctly.
        readback = build_data_inventory("ladies8391", dsn=s.dsn)
        assert "UPLOADED FILES" in readback
        assert "customers.csv" in readback  # "can you see the CSV?" -> yes
        assert "brand-voice.md" in readback  # "the brand voice file?" -> yes
        assert "images uploaded: 1" in readback  # "how many images?" -> 1
        assert "3 file(s)" in readback

        # AGENTS-IN-A-RUN: the parsed content is accessible via the store + the
        # context block injected into the run agents.
        ctx = artifacts.build_artifacts_context("ladies8391", dsn=s.dsn)
        assert "Tone: warm, direct" in ctx  # brand-voice parsed content
        assert "name,email,city" in ctx  # CSV parsed content
        # An agent can also pull full parsed content directly:
        withc = artifacts.list_artifacts("ladies8391", include_content=True, dsn=s.dsn)
        csv_art = next(a for a in withc if a["artifact_type"] == "csv")
        assert "Nadia" in csv_art["parsed_content"]
        # HONESTY: the image carries NO invented visual description.
        assert "visual understanding not captured" in ctx


def test_image_upload_route_registers_countable_artifact(monkeypatch):
    """The POST /studio/upload/image route registers a real, countable image artifact
    (no invented caption). Throwaway tenant + finally-cleanup — never pollutes live."""
    import base64

    import psycopg
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from studio.agui import mount_studio_agui

    tenant = "test_tenant_" + uuid.uuid4().hex[:10]
    monkeypatch.setenv("STUDIO_TENANT_ID", tenant)
    monkeypatch.setenv("ENGINE_DATABASE_URL", DSN)

    app = FastAPI()
    mount_studio_agui(app)
    client = TestClient(app)
    try:
        b64 = base64.b64encode(b"\x89PNG\r\n\x1a\n fake image bytes").decode()
        resp = client.post(
            "/studio/upload/image",
            json={"name": "flash.png", "contentBase64": b64, "mediaType": "image/png"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True and body["type"] == "image" and body["bytes"] > 0

        from studio.artifacts import artifact_inventory

        inv = artifact_inventory(tenant, dsn=DSN)
        assert inv.images == 1
        # A non-image mediaType is rejected (honest guard).
        bad = client.post(
            "/studio/upload/image",
            json={"name": "x.txt", "contentBase64": b64, "mediaType": "text/plain"},
        )
        assert bad.status_code == 400

        # N1: a full data-URI payload is accepted (prefix stripped, not decoded to garbage).
        data_uri = f"data:image/jpeg;base64,{b64}"
        duri = client.post(
            "/studio/upload/image",
            json={"name": "portfolio.jpg", "contentBase64": data_uri},
        )
        assert duri.status_code == 200, duri.text
        assert duri.json()["bytes"] > 0
        assert artifact_inventory(tenant, dsn=DSN).images == 2  # both images counted
    finally:
        with psycopg.connect(DSN, autocommit=True) as c:
            c.execute("DELETE FROM context_artifacts WHERE tenant_id = %s", (tenant,))
