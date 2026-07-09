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


def test_media_type_is_sniffed_from_content_not_extension(monkeypatch, tmp_path):
    """The vision API 400s on a media-type/extension mismatch. Real assets carry
    misnamed files (a JPEG saved as .PNG; a HEIC saved as .jpg), so the true type must
    come from the file's MAGIC BYTES, not its extension: the JPEG-as-.png is sent as
    image/jpeg and ingested; the HEIC-as-.jpg is an honest skip, never a 400. Regression
    guard for the 3 Flash-Tattoos files that 400'd (IMG_3426/3427.PNG, IMG_1585.jpg)."""
    (tmp_path / "mislabeled.png").write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 64)      # JPEG bytes
    (tmp_path / "apple.jpg").write_bytes(b"\x00\x00\x00\x20ftypheic" + b"\x00" * 64)   # HEIC bytes
    _wire_store(monkeypatch)
    seen: dict = {}

    def _capture(data, *, media_type=None, filename=None, **k):
        seen[filename] = media_type
        return _ANALYSIS
    rep = ip.ingest_portfolio("skindesign", "natalie", tmp_path, analyze_fn=_capture).to_dict()
    # content wins over the .png extension -> analyzed as image/jpeg (what the bytes are)
    assert seen.get("mislabeled.png") == "image/jpeg"
    assert {r["file"] for r in rep["ingested"]} == {"mislabeled.png"}
    # HEIC-bytes-as-.jpg is caught by the content sniff -> honest skip, not an API 400
    skip = [s for s in rep["skipped"] if s["file"] == "apple.jpg"]
    assert skip and "convert" in skip[0]["reason"].lower()
    assert "apple.jpg" not in seen  # never sent to the API


@pytest.fixture
def nested_portfolio(tmp_path):
    """The real Flash-Tattoos shape: themed SUBFOLDERS, a basename that COLLIDES across
    two folders, and one unsupported (.heif) export."""
    (tmp_path / "top.jpg").write_bytes(b"\xff\xd8\xff\xe0top")
    pride = tmp_path / "Pride Flash"
    pride.mkdir()
    (pride / "IMG_1.jpg").write_bytes(b"\xff\xd8\xff\xe0pride1")
    (pride / "dup.png").write_bytes(b"\x89PNGpride")        # collides with july/dup.png
    july = tmp_path / "4th of July Flash"
    july.mkdir()
    (july / "dup.png").write_bytes(b"\x89PNGjuly")          # same name, different folder
    (july / "IMG_1585.heif").write_bytes(b"heic-bytes")     # unsupported -> honest skip
    return tmp_path


def test_ingest_recurses_subfolders_with_collision_safe_refs(monkeypatch, nested_portfolio):
    """Themed subfolders are one catalog (recurse); same-named files in different folders
    stay DISTINCT rows (relative-path image_ref, not bare basename); an unsupported format
    is an honest skip, not a silent drop. Regression guard for the 157-flash real ingest."""
    stored = _wire_store(monkeypatch)
    rep = ip.ingest_portfolio(
        "skindesign", "natalie", nested_portfolio, is_test=False,
        analyze_fn=lambda *a, **k: _ANALYSIS,
    )
    d = rep.to_dict()
    # 5 image-like files found recursively; 4 supported ingested; the .heif skipped.
    assert d["scanned"] == 5
    assert d["n_ingested"] == 4
    assert d["n_skipped"] == 1
    heif = d["skipped"][0]
    assert heif["file"] == "4th of July Flash/IMG_1585.heif"
    assert "unsupported" in heif["reason"].lower()
    # the two colliding 'dup.png' resolve to DISTINCT, subfolder-qualified refs — no
    # ON CONFLICT overwrite, so every catalog image is its own row.
    refs = {r["image_ref"] for r in stored}
    assert "upload://natalie/Pride Flash/dup.png" in refs
    assert "upload://natalie/4th of July Flash/dup.png" in refs
    assert len(refs) == 4
    # report 'file' fields are subfolder-qualified relative paths, never bare basenames.
    assert "Pride Flash/IMG_1.jpg" in {r["file"] for r in d["ingested"]}


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
