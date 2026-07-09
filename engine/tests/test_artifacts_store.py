"""Universal context-artifact registry (nmh.4) — pure readback + PG store.

The registry is the single source of truth for "what files exist" that the voice
supervisor AND every campaign agent read. These tests prove:

* the honest readback (:func:`build_artifacts_readback`) states real counts by
  type + the total image count, and says "none uploaded" / "couldn't read"
  without fabricating a file (PURE — no DB);
* register / list / get / deactivate / inventory round-trip on a real Postgres
  private schema (no live-DB pollution, wwy.9 pattern);
* the image path registers a countable, previewable artifact with an HONEST
  empty parsed_content (no invented caption).
"""

from __future__ import annotations

import os

import pytest

from studio.artifacts import (
    ArtifactInventory,
    build_artifacts_context,
    build_artifacts_readback,
)

# ── PURE readback (no DB) ─────────────────────────────────────────────────────


def test_readback_states_real_counts_by_type_and_images():
    inv = ArtifactInventory(
        tenant_id="t",
        total=4,
        by_type={"csv": 1, "brand_voice": 1, "image": 2},
        names_by_type={
            "csv": ["customers.csv"],
            "brand_voice": ["voice.md"],
            "image": ["a.png", "b.jpg"],
        },
    )
    out = build_artifacts_readback(inv)
    assert "4 file(s)" in out
    assert "customers.csv" in out and "voice.md" in out
    assert "images uploaded: 2" in out
    assert inv.images == 2


def test_readback_empty_tenant_is_honest_not_fabricated():
    out = build_artifacts_readback(ArtifactInventory(tenant_id="t"))
    assert "no files are uploaded" in out.lower()
    assert "invent" not in out.lower() or "never claim" in out.lower()


def test_readback_unreadable_store_refuses_to_quote():
    out = build_artifacts_readback(ArtifactInventory(tenant_id="t", readable=False))
    assert "could not read" in out.lower()


def test_images_property_sums_image_artwork_screenshot():
    inv = ArtifactInventory(
        tenant_id="t", total=3, by_type={"image": 1, "artwork": 1, "screenshot": 1}
    )
    assert inv.images == 3


# ── PG store (private schema — never touches the live registry) ───────────────

pg = pytest.mark.skipif(
    not os.environ.get("ENGINE_DATABASE_URL"),
    reason="requires Postgres (set ENGINE_DATABASE_URL)",
)


@pg
def test_register_list_get_deactivate_roundtrip():
    from tests.conftest import private_schema
    from studio import artifacts

    with private_schema("20-context-artifacts.sql") as s:
        aid = artifacts.register_artifact(
            "t",
            "customers.csv",
            "csv",
            media_type="text/csv",
            summary="CSV: 500 rows; columns: name, email",
            parsed_content="name,email\nA,a@x.com",
            meta={"rows": 500},
            dsn=s.dsn,
        )
        assert aid.startswith("art_")
        # list (compact, no content)
        lst = artifacts.list_artifacts("t", dsn=s.dsn)
        assert len(lst) == 1 and lst[0]["artifact_type"] == "csv"
        assert lst[0]["meta"]["rows"] == 500
        assert "parsed_content" not in lst[0]  # compact list omits content
        # list with content (the agent-access path)
        withc = artifacts.list_artifacts("t", include_content=True, dsn=s.dsn)
        assert withc[0]["parsed_content"].startswith("name,email")
        # get (full row incl parsed content)
        got = artifacts.get_artifact(aid, dsn=s.dsn)
        assert got["name"] == "customers.csv" and got["parsed_content"].startswith("name,email")
        # deactivate -> drops from list
        assert artifacts.deactivate_artifact("t", aid, dsn=s.dsn) is True
        assert artifacts.list_artifacts("t", dsn=s.dsn) == []
        # second deactivate is a no-op (real-only, no silent success)
        assert artifacts.deactivate_artifact("t", aid, dsn=s.dsn) is False


@pg
def test_inventory_counts_by_type_and_images():
    from tests.conftest import private_schema
    from studio import artifacts

    with private_schema("20-context-artifacts.sql") as s:
        artifacts.register_artifact("t", "customers.csv", "csv", dsn=s.dsn)
        artifacts.register_artifact("t", "voice.md", "brand_voice", dsn=s.dsn)
        artifacts.register_artifact("t", "flash1.png", "image", dsn=s.dsn)
        artifacts.register_artifact("t", "flash2.png", "artwork", dsn=s.dsn)
        inv = artifacts.artifact_inventory("t", dsn=s.dsn)
        assert inv.total == 4
        assert inv.by_type == {"csv": 1, "brand_voice": 1, "image": 1, "artwork": 1}
        assert inv.images == 2  # image + artwork
        readback = build_artifacts_readback(inv)
        assert "images uploaded: 2" in readback


@pg
def test_image_artifact_has_honest_empty_parsed_content():
    from tests.conftest import private_schema
    from studio import artifacts

    with private_schema("20-context-artifacts.sql") as s:
        artifacts.register_artifact(
            "t",
            "sleeve.png",
            "image",
            media_type="image/png",
            preview="data:image/png;base64,iVBOR",
            meta={"bytes": 2048},
            dsn=s.dsn,
        )
        ctx = build_artifacts_context("t", dsn=s.dsn)
        assert "sleeve.png" in ctx
        # HONESTY: an un-captioned image must not get an invented description.
        assert "visual understanding not captured" in ctx


@pg
def test_register_is_idempotent_on_id():
    from tests.conftest import private_schema
    from studio import artifacts

    with private_schema("20-context-artifacts.sql") as s:
        a1 = artifacts.register_artifact(
            "t", "c.csv", "csv", summary="v1", artifact_id="art_fixed", dsn=s.dsn
        )
        a2 = artifacts.register_artifact(
            "t", "c.csv", "csv", summary="v2 refreshed", artifact_id="art_fixed", dsn=s.dsn
        )
        assert a1 == a2 == "art_fixed"
        rows = artifacts.list_artifacts("t", dsn=s.dsn)
        assert len(rows) == 1 and rows[0]["summary"] == "v2 refreshed"  # refreshed, not duped


@pg
def test_oversize_preview_dropped_but_artifact_registered():
    from tests.conftest import private_schema
    from studio import artifacts

    with private_schema("20-context-artifacts.sql") as s:
        big = "data:image/png;base64," + ("A" * 300_000)
        aid = artifacts.register_artifact("t", "huge.png", "image", preview=big, dsn=s.dsn)
        got = artifacts.get_artifact(aid, dsn=s.dsn)
        assert got["preview"] is None  # dropped
        assert "preview_omitted" in got["meta"]  # noted honestly
        assert artifacts.artifact_inventory("t", dsn=s.dsn).images == 1  # still countable


@pg
def test_bad_artifact_type_rejected():
    from studio import artifacts

    with pytest.raises(ValueError):
        artifacts.register_artifact("t", "x", "not-a-type")


@pg
def test_content_limit_bounds_parsed_content_in_sql():
    """S1: the per-turn context path fetches only the first N chars, so a large CSV's
    full text never enters host context."""
    from tests.conftest import private_schema
    from studio import artifacts

    with private_schema("20-context-artifacts.sql") as s:
        big = "col\n" + ("x" * 5000)
        artifacts.register_artifact("t", "big.csv", "csv", parsed_content=big, dsn=s.dsn)
        rows = artifacts.list_artifacts("t", include_content=True, content_limit=100, dsn=s.dsn)
        assert len(rows[0]["parsed_content"]) == 100  # bounded in SQL
        # full-content path (no limit) still returns everything for a genuine agent read
        full = artifacts.list_artifacts("t", include_content=True, dsn=s.dsn)
        assert len(full[0]["parsed_content"]) == len(big)


@pg
def test_cross_tenant_id_collision_does_not_clobber(monkeypatch):
    """S3: a register with an id that already exists under ANOTHER tenant is a safe
    no-op — the other tenant's content is untouched."""
    from tests.conftest import private_schema
    from studio import artifacts

    with private_schema("20-context-artifacts.sql") as s:
        artifacts.register_artifact(
            "tenant_a",
            "a.csv",
            "csv",
            summary="A's data",
            artifact_id="art_shared",
            dsn=s.dsn,
        )
        # tenant_b tries to register the SAME id — must not overwrite A's row.
        artifacts.register_artifact(
            "tenant_b",
            "b.csv",
            "csv",
            summary="B's data",
            artifact_id="art_shared",
            dsn=s.dsn,
        )
        got = artifacts.get_artifact("art_shared", dsn=s.dsn)
        assert got["tenant_id"] == "tenant_a" and got["summary"] == "A's data"  # untouched
        assert artifacts.list_artifacts("tenant_b", dsn=s.dsn) == []  # B registered nothing visible
