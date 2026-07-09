"""Library READ API contracts (engine-core item 2, real PG).

Asserts the EXACT response shapes the frontend binds:

* GET  /studio/artifacts?kind=&artist=  -> {"artifacts": [{id, kind, name, createdAt,
  artist, vlmStatus, hasPreview}]}
* GET  /studio/artifacts/{id}/raw       -> stored bytes + correct content-type; 404 none
* GET  /studio/artists                  -> {"artists": [{slug, name, studios,
  artworkCount, campaignCount, memoryCount}]}
* GET  /studio/artists/{slug}           -> {"artist": {slug, name, email, phone, studios,
  styleTags, artworks, campaigns, memories}} — every field real, [] when no data
* POST /studio/artists/{slug}/memory    -> {ok, memoryId}

Throwaway tenant + finally-cleanup; the real skindesign data is never touched.
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

PNG = b"\x89PNG\r\n\x1a\n" + b"library-api test bytes"


def _cleanup(tenant: str) -> None:
    import psycopg

    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute("DELETE FROM context_artifacts WHERE tenant_id=%s", (tenant,))
        c.execute("DELETE FROM memories WHERE tenant_id=%s", (tenant,))
        c.execute(
            "DELETE FROM campaign_examples WHERE tenant_id=%s", (tenant,)
        )
        c.execute("DELETE FROM artists WHERE tenant_id=%s", (tenant,))
        c.execute("DELETE FROM assets WHERE campaign_id=%s", (f"portfolio:{tenant}",))


def _seed(tenant: str) -> None:
    import psycopg

    with psycopg.connect(DSN, autocommit=True) as c:
        aid = f"art_test_{uuid.uuid4().hex[:12]}"
        c.execute(
            "INSERT INTO artists (id, tenant_id, name, email, phone) "
            "VALUES (%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING",
            (aid, tenant, "Bella", "bella@example.com", "+15550000000"),
        )
        c.execute(
            "INSERT INTO artist_studios (artist_id, studio_name) VALUES (%s,%s) "
            "ON CONFLICT DO NOTHING",
            (aid, "Skin Design Tattoo Test"),
        )
        c.execute(
            "INSERT INTO campaign_examples (id, tenant_id, campaign_name, artist_name, "
            "offer_price_usd, message_copy, cta, sent_at, delivered_count, failed_count, "
            "dnd_blocked_count) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (tenant_id, campaign_name) DO NOTHING",
            (f"cex_test_{uuid.uuid4().hex[:10]}", tenant, "06.29 BELLA", "Bella",
             1200, "Bella has one opening", "Reply YES", "06.29 2:15 pm", 41, 2, 3),
        )


def _client(monkeypatch, tenant, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from studio.agui import mount_studio_agui
    from studio.console_api import mount_console_api

    monkeypatch.setenv("STUDIO_TENANT_ID", tenant)
    monkeypatch.setenv("ENGINE_DATABASE_URL", DSN)
    monkeypatch.setenv("SCALERS_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("SCALERS_EMBEDDER", "deterministic")
    app = FastAPI()
    mount_studio_agui(app)
    mount_console_api(app)
    return TestClient(app)


def test_library_api_shapes(monkeypatch, tmp_path):
    from studio import image_ingest

    tenant = "test_libapi_" + uuid.uuid4().hex[:8]
    _seed(tenant)
    monkeypatch.setattr(
        image_ingest,
        "analyze_image",
        lambda *a, **k: {
            "status": "ok",
            "tags": {"styles": ["fine-line"], "motifs": ["peony"],
                     "color_mode": "black-and-grey", "mood": "calm",
                     "complexity": "simple", "campaign_fit": [], "other": []},
            "facts_text": "[style] fine-line",
            "summary": "fine-line; motif: peony; black-and-grey",
            "model": "test-model", "fact_count": 2, "error": None,
        },
    )
    client = _client(monkeypatch, tenant, tmp_path)
    try:
        up = client.post(
            "/studio/upload/image",
            json={"name": "peony.png",
                  "contentBase64": base64.b64encode(PNG).decode(),
                  "mediaType": "image/png", "kind": "artwork",
                  "artist": "Bella", "prompt": "flash for Bella"},
        )
        assert up.status_code == 200, up.text
        art_id = up.json()["id"]

        # ---- GET /studio/artifacts (+ kind & artist filters) ---------------- #
        listing = client.get("/studio/artifacts").json()
        assert set(listing.keys()) == {"artifacts"}
        entry = next(a for a in listing["artifacts"] if a["id"] == art_id)
        assert set(entry.keys()) == {
            "id", "kind", "name", "createdAt", "artist", "vlmStatus", "hasPreview"
        }
        assert entry["kind"] == "artwork" and entry["artist"] == "bella"
        assert entry["vlmStatus"] == "ok" and entry["hasPreview"] is True
        assert client.get("/studio/artifacts", params={"kind": "csv"}).json()["artifacts"] == []
        assert (
            client.get("/studio/artifacts", params={"artist": "bella"}).json()["artifacts"][0]["id"]
            == art_id
        )
        assert client.get("/studio/artifacts", params={"artist": "nobody"}).json()["artifacts"] == []

        # ---- GET /studio/artifacts/{id}/raw --------------------------------- #
        raw = client.get(f"/studio/artifacts/{art_id}/raw")
        assert raw.status_code == 200
        assert raw.content == PNG
        assert raw.headers["content-type"].startswith("image/png")
        assert client.get("/studio/artifacts/art_missing/raw").status_code == 404

        # ---- GET /studio/artists --------------------------------------------- #
        roster = client.get("/studio/artists").json()
        assert set(roster.keys()) == {"artists"}
        bella = next(a for a in roster["artists"] if a["slug"] == "bella")
        assert set(bella.keys()) == {
            "slug", "name", "studios", "artworkCount", "campaignCount", "memoryCount"
        }
        assert bella["studios"] == ["Skin Design Tattoo Test"]
        assert bella["artworkCount"] == 1 and bella["campaignCount"] == 1
        assert bella["memoryCount"] == 1  # the upload memory

        # ---- POST /studio/artists/{slug}/memory ------------------------------- #
        note = client.post("/studio/artists/bella/memory", json={"text": "prefers florals"})
        assert note.status_code == 200
        body = note.json()
        assert body["ok"] is True and body["memoryId"].startswith("mem_")
        assert client.post(
            "/studio/artists/bella/memory", json={"text": "  "}
        ).status_code == 400
        assert client.post(
            "/studio/artists/ghost/memory", json={"text": "x"}
        ).status_code == 404

        # ---- GET /studio/artists/{slug} ---------------------------------------- #
        detail = client.get("/studio/artists/bella").json()["artist"]
        assert set(detail.keys()) == {
            "slug", "name", "email", "phone", "studios", "styleTags",
            "artworks", "campaigns", "memories",
        }
        assert detail["email"] == "bella@example.com"
        assert detail["styleTags"] == ["fine-line"]  # from REAL artwork VLM tags
        aw = detail["artworks"][0]
        assert set(aw.keys()) == {"assetId", "artifactId", "styles", "motifs",
                                  "vlmSummary", "why"}
        assert aw["artifactId"] == art_id and aw["why"] is None
        camp = detail["campaigns"][0]
        assert camp["name"] == "06.29 BELLA" and camp["offer_price_usd"] == 1200.0
        assert camp["delivered_count"] == 41 and camp["failed_count"] == 2
        assert camp["dnd_blocked_count"] == 3 and camp["cta"] == "Reply YES"
        mem_texts = [m["text"] for m in detail["memories"]]
        assert "prefers florals" in mem_texts
        assert all(set(m.keys()) == {"at", "text"} for m in detail["memories"])
        # 404 for an unknown slug.
        assert client.get("/studio/artists/ghost").status_code == 404
    finally:
        _cleanup(tenant)


def test_artist_detail_honest_empty(monkeypatch, tmp_path):
    """An artist with no artwork / campaigns / memories reads honest empties."""
    tenant = "test_libempty_" + uuid.uuid4().hex[:8]
    _seed(tenant)
    client = _client(monkeypatch, tenant, tmp_path)
    try:
        detail = client.get("/studio/artists/bella").json()["artist"]
        assert detail["styleTags"] == [] and detail["artworks"] == []
        assert detail["memories"] == []
        roster = client.get("/studio/artists").json()["artists"]
        assert roster[0]["artworkCount"] == 0 and roster[0]["memoryCount"] == 0
    finally:
        _cleanup(tenant)
