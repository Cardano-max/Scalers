"""Competitor post IMAGE research (meeting ask: study their winning posts).

An uploaded competitor screenshot must be VLM-analyzed (the IMAGE is the
research object), filed as a real ``competitor_posts`` row whose visual_tags
come from the model — and it must NEVER leak into our artwork library or an
artist's memory. Runs against the real local Postgres; the VLM seam
(``studio.image_ingest.analyze_image``) is monkeypatched — no network.
"""

from __future__ import annotations

import base64
import os
import uuid

import psycopg
import pytest

DSN = os.environ.get(
    "ENGINE_DATABASE_URL", "postgresql://scalers:scalers@localhost:5432/scalers"
)


def _require_db() -> None:
    try:
        with psycopg.connect(DSN, connect_timeout=3) as conn:
            conn.execute("SELECT 1")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"local Postgres not reachable ({exc})", allow_module_level=True)


_require_db()

# 1×1 PNG — real decodable image bytes.
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAE"
    "hQGAhKmMIQAAAABJRU5ErkJggg=="
)

_VLM_OK = {
    "status": "ok",
    "tags": {
        "styles": ["neo-traditional"], "motifs": ["snake", "dagger"],
        "color_mode": "bold color", "mood": "dramatic",
        "complexity": "detailed", "campaign_fit": ["flash-drop"],
        "other": [],
    },
    "facts_text": "[style] neo-traditional\n[motif] snake\n[motif] dagger",
    "summary": "neo-traditional; motifs: snake, dagger; bold color",
    "model": "anthropic:claude-haiku-4-5",
    "fact_count": 3,
    "error": None,
}


def _cleanup(tenant: str) -> None:
    with psycopg.connect(DSN, autocommit=True) as c:
        for tbl, col in (
            ("competitor_posts", "tenant_id"),
            ("context_artifacts", "tenant_id"),
            ("memories", "tenant_id"),
        ):
            try:
                c.execute(f"DELETE FROM {tbl} WHERE {col}=%s", (tenant,))
            except Exception:
                pass
        try:
            c.execute("DELETE FROM assets WHERE campaign_id=%s", (f"portfolio:{tenant}",))
        except Exception:
            pass


def _upload(tenant: str, monkeypatch, tmp_path, *, prompt: str | None, vlm: dict):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from studio import image_ingest
    from studio.agui import mount_studio_agui

    monkeypatch.setenv("STUDIO_TENANT_ID", tenant)
    monkeypatch.setenv("ENGINE_DATABASE_URL", DSN)
    monkeypatch.setenv("SCALERS_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("SCALERS_EMBEDDER", "deterministic")
    monkeypatch.setattr(image_ingest, "analyze_image", lambda *a, **k: dict(vlm))
    app = FastAPI()
    mount_studio_agui(app)
    client = TestClient(app)
    payload: dict = {
        "name": "winner.png",
        "contentBase64": base64.b64encode(PNG_BYTES).decode(),
        "mediaType": "image/png",
        "kind": "competitor",
    }
    if prompt is not None:
        payload["prompt"] = prompt
    resp = client.post("/studio/upload/image", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _post_rows(tenant: str) -> list[dict]:
    with psycopg.connect(DSN, autocommit=True) as c:
        cur = c.execute(
            "SELECT id, handle, caption, visual_tags, metrics, source "
            "FROM competitor_posts WHERE tenant_id=%s ORDER BY id",
            (tenant,),
        )
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def test_competitor_screenshot_is_vlm_researched_and_filed(monkeypatch, tmp_path):
    tenant = "test_cmpshot_" + uuid.uuid4().hex[:8]
    try:
        body = _upload(
            tenant, monkeypatch, tmp_path,
            prompt="@inkhaus their spring flash drop", vlm=_VLM_OK,
        )
        cp = body.get("competitorPost")
        assert cp and cp["handle"] == "inkhaus"
        assert cp["caption"] == "their spring flash drop"
        # visual_tags come from the VLM's read of the IMAGE, not typed metadata.
        assert "neo-traditional" in cp["visual_tags"]
        assert "snake" in cp["visual_tags"]
        rows = _post_rows(tenant)
        assert len(rows) == 1
        assert rows[0]["source"] == "screenshot_upload"
        assert "dagger" in rows[0]["visual_tags"]
        # Metrics stay ABSENT — a screenshot proves content, not engagement.
        assert rows[0]["metrics"] == {}
        # NOT our artwork: no library asset, no artist memory, honest note.
        assert body.get("assetId") is None
        assert body.get("memoryId") is None
        assert "competitor" in body["note"].lower()
        assert "not added to your artwork library" in body["note"].lower()
    finally:
        _cleanup(tenant)


def test_competitor_screenshot_reupload_is_idempotent(monkeypatch, tmp_path):
    tenant = "test_cmpshot_" + uuid.uuid4().hex[:8]
    try:
        first = _upload(tenant, monkeypatch, tmp_path, prompt="@inkhaus v1", vlm=_VLM_OK)
        second = _upload(tenant, monkeypatch, tmp_path, prompt="@inkhaus v2 better note", vlm=_VLM_OK)
        assert first["competitorPost"]["post_id"] == second["competitorPost"]["post_id"]
        rows = _post_rows(tenant)
        assert len(rows) == 1  # same bytes → ONE row, refreshed
        assert rows[0]["caption"] == "v2 better note"
    finally:
        _cleanup(tenant)


def test_competitor_screenshot_without_vlm_stays_honest(monkeypatch, tmp_path):
    tenant = "test_cmpshot_" + uuid.uuid4().hex[:8]
    vlm_down = {
        "status": "unavailable", "tags": {}, "facts_text": "", "summary": "",
        "model": None, "fact_count": 0,
        "error": "no ANTHROPIC_API_KEY / anthropic SDK — visual analysis skipped",
    }
    try:
        body = _upload(tenant, monkeypatch, tmp_path, prompt="@rival big reel", vlm=vlm_down)
        rows = _post_rows(tenant)
        assert len(rows) == 1
        assert rows[0]["visual_tags"] == []  # nothing invented
        assert "not captured" in body["note"].lower()
    finally:
        _cleanup(tenant)


def test_screenshot_post_feeds_creative_intelligence_scoring(monkeypatch, tmp_path):
    """The filed screenshot participates in scoring: its VLM tags drive
    theme_relevance for a matching campaign theme."""
    tenant = "test_cmpshot_" + uuid.uuid4().hex[:8]
    try:
        _upload(tenant, monkeypatch, tmp_path, prompt="@inkhaus snake dagger winner", vlm=_VLM_OK)
        from studio.competitor_intel import score_posts

        scored = score_posts(tenant, theme_terms=["neo-traditional", "snake"], dsn=DSN)
        assert len(scored) == 1
        comp = scored[0]["scores"]
        assert comp.get("theme_relevance") is not None
        assert comp["theme_relevance"] > 0
    finally:
        _cleanup(tenant)
