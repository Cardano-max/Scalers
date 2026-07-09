"""Image upload → disk + VLM + artist link (engine-core item 1, real PG).

Covers the reworked ``POST /studio/upload/image`` pipeline:

* bytes land on DISK at ``var/artifacts/{tenant}/{sha256}.{ext}`` (content-addressed,
  never a truncated data-URI column);
* the ``context_artifacts`` row carries metadata + storage_path + a bounded <=64k
  thumbnail (an oversized image stores NO thumbnail rather than truncating);
* a REAL ``assets`` library row is added (artwork_source-style, source='upload') so
  ``artwork_select`` can select the piece;
* an ARTIST memory (subject_type='artist') records the upload when an artist is named;
* HONEST DEGRADATION: with the VLM unavailable, the image + artist + prompt still
  persist with ``vlmStatus='unavailable'`` and ZERO fabricated tags.

The VLM seam (``studio.image_ingest.analyze_image``) is monkeypatched — no network.
Throwaway tenant + finally-cleanup; never pollutes the live tenant.
"""

from __future__ import annotations

import base64
import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("ENGINE_DATABASE_URL"),
    reason="requires Postgres (set ENGINE_DATABASE_URL)",
)

DSN = os.environ.get("ENGINE_DATABASE_URL", "postgresql://scalers:scalers@localhost:5432/scalers")

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"fake image body " * 4


def _cleanup(tenant: str) -> None:
    import psycopg

    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute("DELETE FROM context_artifacts WHERE tenant_id=%s", (tenant,))
        c.execute("DELETE FROM memories WHERE tenant_id=%s", (tenant,))
        c.execute("DELETE FROM artists WHERE tenant_id=%s", (tenant,))
        c.execute("DELETE FROM assets WHERE campaign_id=%s", (f"portfolio:{tenant}",))


def _seed_artist(tenant: str, name: str) -> None:
    import psycopg

    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute(
            "INSERT INTO artists (id, tenant_id, name, email) VALUES (%s,%s,%s,%s) "
            "ON CONFLICT (id) DO NOTHING",
            (f"art_test_{uuid.uuid4().hex[:12]}", tenant, name, "t@example.com"),
        )


def _client(monkeypatch, tenant):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from studio.agui import mount_studio_agui

    monkeypatch.setenv("STUDIO_TENANT_ID", tenant)
    monkeypatch.setenv("ENGINE_DATABASE_URL", DSN)
    app = FastAPI()
    mount_studio_agui(app)
    return TestClient(app)


def test_upload_stores_disk_vlm_asset_and_artist_memory(monkeypatch, tmp_path):
    from studio import image_ingest

    tenant = "test_imgpipe_" + uuid.uuid4().hex[:8]
    monkeypatch.setenv("SCALERS_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("SCALERS_EMBEDDER", "deterministic")
    _seed_artist(tenant, "Bella")

    # Deterministic VLM seam — REAL shape, no network.
    monkeypatch.setattr(
        image_ingest,
        "analyze_image",
        lambda *a, **k: {
            "status": "ok",
            "tags": {
                "styles": ["fine-line"], "motifs": ["peony"],
                "color_mode": "black-and-grey", "mood": "delicate",
                "complexity": "intricate", "campaign_fit": ["artist-spotlight"],
                "other": [],
            },
            "facts_text": "[style] fine-line\n[motif] peony\n[color] black-and-grey",
            "summary": "fine-line; motif: peony; black-and-grey",
            "model": "claude-sonnet-4-5",
            "fact_count": 3,
            "error": None,
        },
    )
    client = _client(monkeypatch, tenant)
    try:
        b64 = base64.b64encode(PNG_BYTES).decode()
        resp = client.post(
            "/studio/upload/image",
            json={
                "name": "peony.png", "contentBase64": b64, "mediaType": "image/png",
                "kind": "artwork", "artist": "bella",
                "prompt": "new fine-line peony flash for Bella",
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True and body["type"] == "artwork"
        assert body["bytes"] == len(PNG_BYTES)
        # Disk: real bytes at var/artifacts/{tenant}/{sha}.{ext}.
        assert os.path.isfile(body["storagePath"])
        with open(body["storagePath"], "rb") as f:
            assert f.read() == PNG_BYTES
        assert body["sha256"] in body["storagePath"]
        # VLM landed as REAL tags + artifact parsed_content.
        assert body["vlmStatus"] == "ok"
        assert body["vlm"]["styles"] == ["fine-line"]
        assert body["artist"]["resolved"] is True and body["artist"]["slug"] == "bella"

        from studio.artifacts import get_artifact

        art = get_artifact(body["id"], dsn=DSN)
        assert art is not None
        assert "[style] fine-line" in (art["parsed_content"] or "")
        assert art["meta"]["storage_path"] == body["storagePath"]
        assert art["preview"]  # small image keeps its thumbnail
        assert art["linked_entity_type"] == "artist" and art["linked_entity_id"] == "bella"

        # Library row is selectable by artwork_select.
        from studio.artwork_select import list_artwork, select_artwork

        pieces = list_artwork(tenant, "Bella", dsn=DSN)
        assert [p.asset_id for p in pieces] == [body["assetId"]]
        pick = select_artwork(pieces, artist_styles=["fine-line"], theme_terms=["peony"])
        assert pick is not None and pick.exact_match and pick.asset_id == body["assetId"]

        # Artist memory recorded with the VLM one-liner + operator prompt.
        from studio.artist_memory import list_artist_memories

        mems = list_artist_memories(tenant, "bella", dsn=DSN)
        assert len(mems) == 1
        assert "New design uploaded: fine-line" in mems[0]["text"]
        assert "operator prompt: new fine-line peony flash" in mems[0]["text"]

        # Re-upload of the SAME bytes refreshes (no duplicate artifact/asset).
        again = client.post(
            "/studio/upload/image",
            json={"name": "peony.png", "contentBase64": b64, "mediaType": "image/png",
                  "kind": "artwork", "artist": "Bella"},
        )
        assert again.status_code == 200 and again.json()["id"] == body["id"]
        assert len(list_artwork(tenant, "Bella", dsn=DSN)) == 1
    finally:
        _cleanup(tenant)


def test_upload_degrades_honestly_when_vlm_unavailable(monkeypatch, tmp_path):
    tenant = "test_imgdeg_" + uuid.uuid4().hex[:8]
    monkeypatch.setenv("SCALERS_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("SCALERS_EMBEDDER", "deterministic")
    # Force the unconfigured path (no key/SDK — the seam's own honest branch).
    monkeypatch.setattr("studio.ingest_vlm.is_configured", lambda: False)

    client = _client(monkeypatch, tenant)
    try:
        b64 = base64.b64encode(PNG_BYTES).decode()
        resp = client.post(
            "/studio/upload/image",
            json={"name": "sleeve.png", "contentBase64": b64, "mediaType": "image/png",
                  "artist": "Nobody Known", "prompt": "dragon sleeve idea"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True
        assert body["vlmStatus"] == "unavailable"
        assert body["vlm"] is None  # ZERO fabricated tags
        assert "NOT captured" in body["note"]
        assert os.path.isfile(body["storagePath"])  # image still stored
        # Unresolved artist is an HONEST miss, still recorded as input + slug.
        assert body["artist"]["resolved"] is False
        assert body["artist"]["slug"] == "nobody-known"
        # The library row exists but carries NO invented tags.
        from studio.artwork_select import list_artwork

        pieces = list_artwork(tenant, dsn=DSN)
        assert len(pieces) == 1 and pieces[0].styles == [] and pieces[0].motifs == []
        # Memory still records the upload, honestly labelled.
        from studio.artist_memory import list_artist_memories

        mems = list_artist_memories(tenant, "nobody-known", dsn=DSN)
        assert len(mems) == 1 and "visual analysis unavailable" in mems[0]["text"]
        assert "operator prompt: dragon sleeve idea" in mems[0]["text"]
    finally:
        _cleanup(tenant)


def test_oversize_image_stores_no_truncated_thumbnail(monkeypatch, tmp_path):
    tenant = "test_imgbig_" + uuid.uuid4().hex[:8]
    monkeypatch.setenv("SCALERS_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setattr("studio.ingest_vlm.is_configured", lambda: False)
    client = _client(monkeypatch, tenant)
    try:
        big = b"\x89PNG\r\n\x1a\n" + os.urandom(80_000)  # data-URI > 64k chars
        resp = client.post(
            "/studio/upload/image",
            json={"name": "huge.png",
                  "contentBase64": base64.b64encode(big).decode(),
                  "mediaType": "image/png"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["hasPreview"] is False  # no truncated thumbnail
        with open(body["storagePath"], "rb") as f:
            assert f.read() == big  # FULL bytes on disk — nothing lost

        from studio.artifacts import get_artifact

        art = get_artifact(body["id"], dsn=DSN)
        assert art["preview"] is None
        assert "preview_omitted" in art["meta"]
    finally:
        _cleanup(tenant)
