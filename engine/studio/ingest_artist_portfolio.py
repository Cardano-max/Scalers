"""Turnkey artist-portfolio ingest + top-4 campaign selection (CustomerAcq-nmh.5).

Point this at a folder of an artist's artwork images and it:
1. analyzes EACH image with the real VLM (:func:`studio.artwork_vision.analyze_artwork`)
   into structured, sensitive-attribute-gated tags + a summary,
2. embeds + stores each into the artist's tenant-scoped memory
   (:mod:`studio.artwork_memory`),
3. returns an honest, DB-verifiable report (scanned / ingested / skipped-with-reason /
   per-image tags).

Then a campaign can call :func:`shortlist_top4` to get the 4 best-matching pieces for a
motif/theme and a mid-run PAUSE prompt for the operator to choose (spec §9/§10/§22).

Gates: no-fabrication (an image that can't be analyzed is SKIPPED with a reason, never a
guessed row); NO sensitive-attribute inference (the vision gate rejects it upstream);
company-owned assets only (local operator-provided files); HELD — this writes memory and
sends NOTHING.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from studio.artwork_memory import ensure_schema, record_artwork, search_artwork
from studio.artwork_vision import (
    ArtworkAnalysis,
    SensitiveAttributeError,
    analysis_summary,
    analyze_artwork,
)
from studio.ingest_vlm import NotConfiguredError, guess_media_type

# The Anthropic vision API decodes these; HEIC (Apple) must be converted to JPEG first.
SUPPORTED_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif")
# Per-image byte ceiling — the vision API caps ~5MB/image (base64); larger is skipped
# with an honest reason rather than silently truncated.
MAX_IMAGE_BYTES = 5 * 1024 * 1024

AnalyzeFn = Callable[..., ArtworkAnalysis]


@dataclass
class PortfolioIngestReport:
    tenant_id: str
    artist_id: str
    scanned: int = 0
    ingested: list[dict[str, Any]] = field(default_factory=list)   # {file,id,summary,tags}
    skipped: list[dict[str, Any]] = field(default_factory=list)     # {file,reason}

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "artist_id": self.artist_id,
            "scanned": self.scanned,
            "n_ingested": len(self.ingested),
            "n_skipped": len(self.skipped),
            "ingested": self.ingested,
            "skipped": self.skipped,
        }


def _image_files(folder: Path) -> list[Path]:
    return sorted(
        p for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS
    )


def ingest_portfolio(
    tenant_id: str,
    artist_id: str,
    folder: str | Path,
    *,
    source: str = "upload",
    is_test: bool = False,
    analyze_fn: AnalyzeFn | None = None,
    model: str | None = None,
    client: Any = None,
    embedder: Any = None,
    dsn: str | None = None,
) -> PortfolioIngestReport:
    """Ingest every supported image in ``folder`` into ``artist_id``'s memory under
    ``tenant_id``. Returns a :class:`PortfolioIngestReport`.

    Real client work sets ``is_test=False`` (skindesign, the operator's own assets).
    ``analyze_fn`` is injectable for tests; production uses the real
    :func:`studio.artwork_vision.analyze_artwork`. A per-image failure (unconfigured,
    oversized, bad decode, or a sensitive-attribute rejection) SKIPS that image with a
    concrete reason — the run continues and never stores a fabricated row."""
    folder = Path(folder)
    if not folder.is_dir():
        raise NotADirectoryError(f"artist portfolio folder not found: {folder}")
    analyze_fn = analyze_fn or analyze_artwork
    ensure_schema(dsn)

    report = PortfolioIngestReport(tenant_id=tenant_id, artist_id=artist_id)
    for path in _image_files(folder):
        report.scanned += 1
        image_ref = f"{source}://{artist_id}/{path.name}"
        try:
            size = path.stat().st_size
            if size > MAX_IMAGE_BYTES:
                raise ValueError(
                    f"image is {size} bytes > {MAX_IMAGE_BYTES} cap (downscale/convert first)"
                )
            data = path.read_bytes()
            media_type = guess_media_type(path.name)
            analysis = analyze_fn(data, media_type=media_type, filename=path.name,
                                  model=model, client=client)
            rid = record_artwork(
                tenant_id, artist_id, image_ref, analysis,
                source=source, media_type=media_type, is_test=is_test,
                embedder=embedder, dsn=dsn,
            )
            report.ingested.append({
                "file": path.name, "id": rid,
                "summary": analysis_summary(analysis),
                "tags": analysis.model_dump(),
            })
        except SensitiveAttributeError as exc:
            report.skipped.append({"file": path.name, "reason": f"sensitive-attribute rejected: {exc}"})
        except NotConfiguredError as exc:
            report.skipped.append({"file": path.name, "reason": f"VLM unconfigured: {exc}"})
        except Exception as exc:  # honest per-image skip; never a fabricated row
            report.skipped.append({"file": path.name, "reason": f"{type(exc).__name__}: {exc}"})
    return report


# --------------------------------------------------------------------------- #
# Top-4 campaign selection + mid-run pause (spec §9/§10/§22)
# --------------------------------------------------------------------------- #
@dataclass
class Top4Selection:
    query: str
    picks: list[dict[str, Any]] = field(default_factory=list)   # {image_ref,summary,why,similarity}
    honest_empty: bool = False
    pause_prompt: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query, "picks": self.picks,
            "honest_empty": self.honest_empty, "pause_prompt": self.pause_prompt,
        }


def shortlist_top4(
    tenant_id: str,
    artist_id: str,
    campaign_query: str,
    *,
    k: int = 4,
    include_test: bool = False,
    embedder: Any = None,
    dsn: str | None = None,
) -> Top4Selection:
    """Shortlist the top-``k`` (default 4) artworks matching ``campaign_query`` and
    build the mid-run PAUSE prompt the operator answers. NEVER auto-picks — it returns
    the ranked options + the question. When the artist has no matching artwork, returns
    ``honest_empty`` with the spec's honest message (never a random attach)."""
    hits = search_artwork(
        tenant_id, artist_id, campaign_query, k=k,
        include_test=include_test, embedder=embedder, dsn=dsn,
    )
    if not hits:
        return Top4Selection(
            query=campaign_query, honest_empty=True,
            pause_prompt=(
                f"I could not find a good matching artwork for '{campaign_query}' in "
                f"{artist_id}'s portfolio. Please upload one, or tell me to use a general "
                "artist piece."
            ),
        )
    picks = [{
        "image_ref": h.record.image_ref,
        "summary": h.record.summary,
        "why": f"matches on {', '.join(h.record.analysis.style_tags[:4]) or h.record.analysis.motif}",
        "similarity": round(h.similarity, 4),
    } for h in hits]
    n = len(picks)
    prompt = (
        f"I found {n} matching piece(s) for '{campaign_query}'. Which one should I use "
        f"for this campaign? " + "; ".join(
            f"[{i + 1}] {p['summary']}" for i, p in enumerate(picks)
        )
    )
    return Top4Selection(query=campaign_query, picks=picks, pause_prompt=prompt)


def cli_ingest(argv: list[str] | None = None) -> int:
    """A fresh session can run the real ingest once the portfolio lands:

        STUDIO_TENANT_ID=skindesign ANTHROPIC_API_KEY=... \\
          uv run python -m studio.ingest_artist_portfolio <artist_id> <folder>
    """
    import json
    import sys

    args = argv if argv is not None else sys.argv[1:]
    if len(args) < 2:
        print("usage: python -m studio.ingest_artist_portfolio <artist_id> <folder> "
              "[--test]", file=sys.stderr)
        return 2
    artist_id, folder = args[0], args[1]
    is_test = "--test" in args[2:]
    tenant = os.environ.get("STUDIO_TENANT_ID", "skindesign")
    report = ingest_portfolio(tenant, artist_id, folder, is_test=is_test)
    print(json.dumps(report.to_dict(), indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(cli_ingest())
