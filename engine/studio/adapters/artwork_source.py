"""Artwork source adapter — real studio FLASH artwork into the portfolio library.

``CsvArtworkSource`` parses a studio artwork CSV (works now) into normalized
:class:`CatalogArtwork` rows, and :func:`seed_artwork_from_csv` writes them into the
SAME ``assets`` library rows the drafter reads (:mod:`studio.artwork_select`) —
``asset_type='studio_artwork'``, ``status='library'``, ``campaign_id='portfolio:{tenant}'``,
``content.source='csv'`` (+ ``content.collection``). This mirrors ``seed_studio_artwork``
but for FIRST-PARTY real artwork:

  * The tags are the studio's OWN (copied verbatim from the CSV) — this is NOT a claim
    that a vision model auto-tagged anything. The P4 VLM tagger enriches these same rows
    later; here ``source='csv'`` keeps the provenance honest and distinguishes real
    artwork from the ``source='seed'`` mock portfolio (and outranks it on selection ties).
  * Ids are deterministic (a stable hash of ``image_ref``) and the insert is idempotent
    (``record_asset`` does ``ON CONFLICT (id) DO NOTHING``), so re-loading the same CSV is
    a true no-op — same asset ids, no duplicates.
  * Parsing is tolerant: a row missing ``artist`` or ``image_ref`` is skipped (never a
    fabricated piece); a malformed ``styles``/``motifs`` cell degrades to ``[]`` rather
    than crashing. An empty / header-only CSV yields ZERO rows — an honest empty
    portfolio, never invented artwork.

The Mini-App / VLM enrichment paths remain the future seam (see
:mod:`studio.adapters.artist_source`); this adapter is the CSV-today implementation.
"""

from __future__ import annotations

import csv
import hashlib
import io
import os
from dataclasses import dataclass, field
from typing import Any, Iterator, Protocol, runtime_checkable

from studio.artwork_select import (
    ARTWORK_ASSET_TYPE,
    ARTWORK_STATUS,
    _portfolio_campaign_id,
)

# Provenance marker written to ``content.source`` for every CSV-ingested piece. Keep in
# sync with the selection tie-break in :func:`studio.artwork_select.select_artwork`.
ARTWORK_SOURCE_CSV = "csv"


def _split_tags(cell: str | None) -> list[str]:
    """Semicolon- (or comma-) delimited tag cell -> clean list. Tolerant: a malformed or
    empty cell degrades to ``[]`` rather than raising. Never fabricates a tag."""
    if not cell:
        return []
    parts = str(cell).replace(",", ";").split(";")
    return [p.strip() for p in parts if p and p.strip()]


def _truthy(cell: str | None) -> bool:
    return str(cell or "").strip().lower() in {"1", "true", "yes", "y", "t"}


@dataclass
class CatalogArtwork:
    """One normalized artwork row from the CSV. Only ``artist`` + ``image_ref`` are
    required; everything else is honestly empty when the CSV did not provide it."""

    artist: str
    image_ref: str
    caption: str = ""
    styles: list[str] = field(default_factory=list)
    motifs: list[str] = field(default_factory=list)
    collection: str = ""
    is_best_example: bool = False

    def asset_id(self, tenant_id: str) -> str:
        """Deterministic, collision-resistant id derived from the (tenant, image_ref) so
        re-loading the same CSV is idempotent. Distinct from the ``art_..._NN`` seed ids."""
        digest = hashlib.sha1(f"{tenant_id}|{self.image_ref}".encode("utf-8")).hexdigest()[:16]
        return f"art_csv_{tenant_id}_{digest}"

    def content(self) -> dict[str, Any]:
        """The ``assets.content`` JSONB blob — the exact shape ``ArtworkRef.from_asset_row``
        reads back. ``source='csv'`` marks first-party provenance (NOT a VLM claim)."""
        return {
            "artist": self.artist,
            "image_ref": self.image_ref,
            "caption": self.caption,
            "styles": list(self.styles),
            "motifs": list(self.motifs),
            "collection": self.collection,
            "is_best_example": self.is_best_example,
            "source": ARTWORK_SOURCE_CSV,
        }


@runtime_checkable
class ArtworkSourceProtocol(Protocol):
    """Yields normalized :class:`CatalogArtwork` rows. ``name`` identifies the source."""

    name: str

    def artworks(self) -> Iterator[CatalogArtwork]:
        ...


class CsvArtworkSource:
    """Normalized studio artwork from a CSV (works now). Columns:
    ``artist, image_ref, caption, styles, motifs, collection, is_best_example``
    (styles/motifs semicolon- or comma-delimited). Tolerant header casing/spacing; a row
    without ``artist`` or ``image_ref`` is skipped rather than fabricated."""

    name = "studio artwork CSV"

    def __init__(self, content: str) -> None:
        self._content = content or ""

    @classmethod
    def from_path(cls, path: str) -> "CsvArtworkSource":
        """Build from a CSV file on disk (utf-8). Honest empty source if unreadable."""
        try:
            with open(path, "r", encoding="utf-8") as f:
                return cls(f.read())
        except OSError:
            return cls("")

    def artworks(self) -> Iterator[CatalogArtwork]:
        text = self._content.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
        if not text.strip():
            return
        for raw in csv.DictReader(io.StringIO(text)):
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
            artist = row.get("artist") or ""
            image_ref = row.get("image_ref") or row.get("image") or row.get("ref") or ""
            if not artist or not image_ref:
                # A row with no identity is not artwork — skip, never fabricate.
                continue
            yield CatalogArtwork(
                artist=artist,
                image_ref=image_ref,
                caption=row.get("caption") or "",
                styles=_split_tags(row.get("styles") or row.get("style")),
                motifs=_split_tags(row.get("motifs") or row.get("motif")),
                collection=row.get("collection") or "",
                is_best_example=_truthy(row.get("is_best_example")),
            )


# --------------------------------------------------------------------------- #
# Ingest — write the CSV artwork into the persistent portfolio library.
# --------------------------------------------------------------------------- #
_DEFAULT_DSN = "postgresql://scalers:scalers@localhost:5432/scalers"


def _dsn(dsn: str | None = None) -> str:
    return dsn or os.environ.get("ENGINE_DATABASE_URL") or _DEFAULT_DSN


def seed_artwork_from_csv(
    source: "CsvArtworkSource | str",
    tenant_id: str = "ladies8391",
    *,
    dsn: str | None = None,
) -> list[str]:
    """Idempotently write CSV artwork into the tenant's ``assets`` portfolio library.

    ``source`` may be a :class:`CsvArtworkSource` or a raw CSV string. Returns the
    deterministic asset ids written (in CSV order). Best-effort on the store: returns
    ``[]`` honestly if it is unavailable — it never fabricates a persisted piece. An
    empty / header-only CSV yields ``[]`` (an honest empty portfolio)."""
    src = source if isinstance(source, CsvArtworkSource) else CsvArtworkSource(source)
    try:
        from team.store import TeamStore

        store = TeamStore(_dsn(dsn))
        store.setup()  # idempotent CREATE TABLE IF NOT EXISTS
    except Exception:
        return []

    campaign_id = _portfolio_campaign_id(tenant_id)
    ids: list[str] = []
    seen: set[str] = set()
    for art in src.artworks():
        aid = art.asset_id(tenant_id)
        if aid in seen:  # same image_ref twice in one CSV -> one library row
            continue
        seen.add(aid)
        try:
            store.record_asset(
                id=aid,
                campaign_id=campaign_id,
                asset_type=ARTWORK_ASSET_TYPE,
                content=art.content(),
                status=ARTWORK_STATUS,
            )
            ids.append(aid)
        except Exception:
            continue
    return ids


if __name__ == "__main__":  # pragma: no cover
    import json

    default_csv = os.path.join(os.path.dirname(__file__), "..", "data", "flash_tattoos_catalog.csv")
    path = os.environ.get("STUDIO_ARTWORK_CSV", os.path.normpath(default_csv))
    tid = os.environ.get("STUDIO_TENANT_ID", "ladies8391")
    seeded = seed_artwork_from_csv(CsvArtworkSource.from_path(path), tid)
    print(json.dumps({"tenant": tid, "csv": path, "seeded_asset_ids": seeded}, indent=2))
