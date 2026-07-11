"""prune_broken_artworks — the dead gallery-card cleaner.

DB-gated: seeds one healthy asset (real temp file), one untagged-with-bytes,
one file-missing orphan and one artifact-missing orphan under a throwaway
tenant, then asserts the classifier tells them apart and --fix deletes ONLY
the orphans (never the healthy or untagged rows)."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import uuid
from pathlib import Path

import pytest

_pg = pytest.mark.skipif(
    not os.environ.get("ENGINE_DATABASE_URL"),
    reason="requires Postgres (set ENGINE_DATABASE_URL)",
)

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "prune_broken_artworks.py"
_spec = importlib.util.spec_from_file_location("prune_broken_artworks", _SCRIPT)
prune_mod = importlib.util.module_from_spec(_spec)
sys.modules["prune_broken_artworks"] = prune_mod
_spec.loader.exec_module(prune_mod)


@pytest.fixture()
def seeded(tmp_path):
    import psycopg

    dsn = os.environ["ENGINE_DATABASE_URL"]
    tenant = f"t_prune_{uuid.uuid4().hex[:8]}"
    camp = f"portfolio:{tenant}"
    real_file = tmp_path / "piece.jpg"
    real_file.write_bytes(b"\xff\xd8\xff\xe0 fake jpeg bytes")

    def _asset(aid, content):
        return (
            "INSERT INTO assets (id, campaign_id, asset_type, content, status) "
            "VALUES (%s, %s, 'artwork_image', %s, 'selected')",
            (aid, camp, json.dumps(content)),
        )

    def _artifact(aid, path):
        return (
            "INSERT INTO context_artifacts (id, tenant_id, name, artifact_type, "
            "summary, source, meta) VALUES (%s, %s, 'x', 'image', 's', 'upload', %s)",
            (aid, tenant, json.dumps({"storage_path": str(path)})),
        )

    ids = {
        "ok": f"art_upload_{tenant}_ok",
        "untagged": f"art_upload_{tenant}_untag",
        "gonefile": f"art_upload_{tenant}_gone",
        "noart": f"art_upload_{tenant}_noart",
    }
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(*_artifact(f"art_img_{tenant}_ok", real_file))
        conn.execute(*_asset(ids["ok"], {
            "artist": "K", "styles": ["realism"], "motifs": ["dragon"],
            "image_ref": f"artifact://art_img_{tenant}_ok"}))
        conn.execute(*_artifact(f"art_img_{tenant}_untag", real_file))
        conn.execute(*_asset(ids["untagged"], {
            "artist": "K", "styles": [], "motifs": [],
            "image_ref": f"artifact://art_img_{tenant}_untag"}))
        conn.execute(*_artifact(f"art_img_{tenant}_gone", tmp_path / "deleted.jpg"))
        conn.execute(*_asset(ids["gonefile"], {
            "artist": "K", "styles": [], "motifs": [],
            "image_ref": f"artifact://art_img_{tenant}_gone"}))
        conn.execute(*_asset(ids["noart"], {
            "artist": "K", "styles": [], "motifs": [],
            "image_ref": f"artifact://art_img_{tenant}_missingrow"}))
    yield tenant, ids
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("DELETE FROM assets WHERE campaign_id = %s", (camp,))
        conn.execute("DELETE FROM context_artifacts WHERE tenant_id = %s", (tenant,))


@_pg
def test_classifier_tells_the_four_classes_apart(seeded):
    tenant, ids = seeded
    by_id = {r["asset_id"]: r["class"] for r in prune_mod.classify_artworks(tenant)}
    assert by_id[ids["ok"]] == "OK"
    assert by_id[ids["untagged"]] == "UNTAGGED"
    assert by_id[ids["gonefile"]] == "ORPHAN_FILE_MISSING"
    assert by_id[ids["noart"]] == "ORPHAN_NO_ARTIFACT"


@_pg
def test_fix_deletes_only_orphans(seeded):
    import psycopg

    tenant, ids = seeded
    out = prune_mod.prune(tenant, fix=True)
    assert out["deleted_assets"] == 2
    assert out["deleted_artifacts"] == 1  # only the dead-file artifact row
    with psycopg.connect(os.environ["ENGINE_DATABASE_URL"], autocommit=True) as conn:
        left = {r[0] for r in conn.execute(
            "SELECT id FROM assets WHERE campaign_id = %s", (f"portfolio:{tenant}",)
        ).fetchall()}
    assert left == {ids["ok"], ids["untagged"]}  # healthy + untagged survive
