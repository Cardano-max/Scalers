"""Artist source adapters — normalized :class:`Artist` profiles + artwork refs.

``CsvArtistSource`` / ``SeededArtistSource`` work now (artist rows from CSV or a seeded
list); ``FutureMiniAppArtistApi`` is an honest stub raising
:class:`~studio.adapters.NotConfiguredError`. The VLM artwork tagging (P4) will enrich
``Artwork.tags`` later; for now ``is_best_example`` is the honest placeholder and tags
are empty rather than fabricated.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from typing import Any, Iterator, Protocol, runtime_checkable

from studio.adapters import NotConfiguredError


@dataclass
class Artwork:
    """One artwork/flash reference for an artist. ``tags`` is VLM output (P4) — empty now,
    never fabricated; ``is_best_example`` is the CSV/seed placeholder used until then."""

    image_ref: str
    caption: str | None = None
    is_best_example: bool = False
    tags: dict[str, Any] = field(default_factory=dict)


@dataclass
class Artist:
    """A normalized artist profile the graph consumes (seeded/CSV now, API later)."""

    name: str
    shop: str | None = None
    styles: list[str] = field(default_factory=list)
    niche: str | None = None
    ig_handle: str | None = None
    artworks: list[Artwork] = field(default_factory=list)


@runtime_checkable
class ArtistSourceProtocol(Protocol):
    """Yields normalized :class:`Artist` profiles."""

    name: str

    def artists(self) -> Iterator[Artist]:
        ...


class SeededArtistSource:
    """Normalized artists from an in-memory seed list (works now, DB-free).

    Used for the demo / tests so category branching that references an artist has a real
    roster to resolve names against, without a client API."""

    name = "seeded artists"

    def __init__(self, seed: list[Artist] | None = None) -> None:
        self._seed = seed or []

    def artists(self) -> Iterator[Artist]:
        yield from self._seed

    def names(self) -> list[str]:
        return [a.name for a in self._seed]


class CsvArtistSource:
    """Normalized artists from a CSV (works now). Columns: name, shop, styles, niche,
    ig/handle. Artwork tagging is deferred (P4) so ``artworks`` stays empty here."""

    name = "artist CSV"

    def __init__(self, content: str) -> None:
        self._content = content or ""

    def artists(self) -> Iterator[Artist]:
        text = self._content.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
        if not text.strip():
            return
        for row in csv.DictReader(io.StringIO(text)):
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
            name = row.get("name") or row.get("artist")
            if not name:
                continue
            styles_raw = row.get("styles") or row.get("style") or ""
            yield Artist(
                name=name,
                shop=row.get("shop") or row.get("studio") or None,
                styles=[s.strip() for s in styles_raw.replace(",", ";").split(";") if s.strip()],
                niche=row.get("niche") or None,
                ig_handle=row.get("ig") or row.get("instagram") or row.get("handle") or None,
            )


class FutureMiniAppArtistApi:
    """STUB: artist profiles + artwork from the Mini-App. Not connected yet -> honest error."""

    name = "Mini-App artist API (not connected)"

    def artists(self) -> Iterator[Artist]:
        raise NotConfiguredError(
            "The Mini-App artist API is not connected yet — seed artists or upload an "
            "artist CSV for now. Artwork VLM tagging arrives in a later phase."
        )
