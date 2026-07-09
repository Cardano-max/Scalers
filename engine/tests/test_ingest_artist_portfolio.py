"""nmh.5 — turnkey portfolio ingest + top-4 selection (hermetic).

The folder-walk, per-image skip-with-reason, and mid-run-pause logic are exercised
offline: the VLM analyzer and the pgvector store are injected/monkeypatched, so no key
or Postgres is needed. (The real pgvector round-trip is in test_artwork_memory_pg.py.)
"""

from __future__ import annotations

import pytest

import studio.ingest_artist_portfolio as ip
from studio.artwork_vision import ArtworkAnalysis, SensitiveAttributeError

_ANALYSIS = ArtworkAnalysis(
    style="black-and-grey realism", motif="lion", color_mode="black-and-grey",
    placement="forearm", vibe="bold and symbolic", linework="crisp", complexity="complex",
    audience_fit="drawn to strength symbolism", campaign_use="full-day booking",
    caption_angle="a resilience statement", style_tags=["lion", "realism"],
)


@pytest.fixture
def portfolio(tmp_path):
    """A folder with 3 real image filenames + 1 non-image + 1 oversized image."""
    (tmp_path / "01_lion.jpg").write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")
    (tmp_path / "02_rose.png").write_bytes(b"\x89PNGfake")
    (tmp_path / "03_wave.webp").write_bytes(b"RIFFfakewebp")
    (tmp_path / "notes.txt").write_text("not an image")           # skipped (unsupported)
    (tmp_path / "huge.jpg").write_bytes(b"x" * (ip.MAX_IMAGE_BYTES + 1))  # skipped (size)
    return tmp_path


def _wire_store(monkeypatch):
    stored: list[dict] = []
    monkeypatch.setattr(ip, "ensure_schema", lambda dsn=None: None)

    def _rec(tenant, artist, image_ref, analysis, **kw):
        rid = f"art_{len(stored)}"
        stored.append({"id": rid, "tenant": tenant, "artist": artist,
                       "image_ref": image_ref, "is_test": kw.get("is_test")})
        return rid
    monkeypatch.setattr(ip, "record_artwork", _rec)
    return stored


def test_ingest_walks_folder_and_skips_non_images_and_oversized(monkeypatch, portfolio):
    stored = _wire_store(monkeypatch)
    rep = ip.ingest_portfolio(
        "skindesign", "keebs", portfolio, is_test=False,
        analyze_fn=lambda *a, **k: _ANALYSIS,
    )
    d = rep.to_dict()
    # 4 image-extension files scanned (txt excluded by the walk); 3 ingested, 1 oversized skipped
    assert d["scanned"] == 4
    assert d["n_ingested"] == 3
    assert {r["file"] for r in d["ingested"]} == {"01_lion.jpg", "02_rose.png", "03_wave.webp"}
    assert d["n_skipped"] == 1 and d["skipped"][0]["file"] == "huge.jpg"
    assert "cap" in d["skipped"][0]["reason"]
    assert all(row["is_test"] is False for row in stored)
    # image_ref is stable + namespaced by source/artist
    assert any("upload://keebs/01_lion.jpg" == r["image_ref"] for r in stored)


def test_sensitive_attribute_image_is_skipped_not_stored(monkeypatch, portfolio):
    stored = _wire_store(monkeypatch)

    def _analyze(*a, **k):
        raise SensitiveAttributeError("age inference in audience_fit")
    rep = ip.ingest_portfolio("skindesign", "keebs", portfolio, analyze_fn=_analyze)
    # every ANALYZABLE image is rejected by the gate -> nothing stored; the 3 real
    # images carry the sensitive-attribute reason (huge.jpg is a separate size skip).
    assert rep.to_dict()["n_ingested"] == 0
    assert stored == []
    sens = [s for s in rep.skipped if "sensitive-attribute" in s["reason"]]
    assert {s["file"] for s in sens} == {"01_lion.jpg", "02_rose.png", "03_wave.webp"}


def test_missing_folder_raises():
    with pytest.raises(NotADirectoryError):
        ip.ingest_portfolio("skindesign", "keebs", "/no/such/folder")


# --- top-4 selection + mid-run pause (search injected) --------------------- #
def _hit(image_ref, summary, tags, sim):
    from studio.artwork_memory import ArtworkHit, ArtworkRecord
    rec = ArtworkRecord(id="x", tenant_id="skindesign", artist_id="keebs",
                        image_ref=image_ref, source="upload", media_type="image/jpeg",
                        tags={**_ANALYSIS.model_dump(), "style_tags": tags},
                        summary=summary, is_test=False)
    return ArtworkHit(record=rec, similarity=sim)


def test_shortlist_top4_builds_pause_prompt(monkeypatch):
    monkeypatch.setattr(ip, "search_artwork", lambda *a, **k: [
        _hit("upload://keebs/01_lion.jpg", "lion realism", ["lion", "realism"], 0.91),
        _hit("upload://keebs/04_lion2.jpg", "lion linework", ["lion", "linework"], 0.82),
    ])
    sel = ip.shortlist_top4("skindesign", "keebs", "lion strength")
    assert not sel.honest_empty
    assert len(sel.picks) == 2
    assert "Which one should I use" in sel.pause_prompt
    assert sel.picks[0]["similarity"] == 0.91
    assert "lion" in sel.picks[0]["why"]


def test_shortlist_honest_empty_when_no_match(monkeypatch):
    monkeypatch.setattr(ip, "search_artwork", lambda *a, **k: [])
    sel = ip.shortlist_top4("skindesign", "keebs", "dragon")
    assert sel.honest_empty and sel.picks == []
    assert "could not find a good matching artwork" in sel.pause_prompt
