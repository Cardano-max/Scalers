"""Backfill portfolio tags from the VLM analysis that already exists.

An uploaded piece is analysed by the VLM and the result is written to
``context_artifacts.parsed_content`` as ``[style] …`` / ``[motif] …`` lines. The artwork
RANKER, however, reads ``styles``/``motifs`` off the ``studio_artwork`` asset row — and the
upload path never copied the tags across. So Keebs' real portfolio sat in the pool with
EMPTY tags: the single most botanical piece in the library ("Neo-traditional; motif: Dahlia
flower, Sunflowers, Bees") was invisible to matching, and a "fine-line botanical" brief
offered a top-4 of Spider-Man masks and dragons instead — every one of them a real piece,
honestly explained, and completely wrong.

This copies the tags the VLM already produced onto the asset rows. It invents nothing: a
piece with no analysis stays untagged, and only empty tag arrays are filled (an existing
tag is never overwritten). Idempotent.

    uv run python scripts/backfill_artwork_tags.py [--tenant skindesign] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg  # noqa: E402

#: '[style] geometric blackwork' / '[motif] Spider-Man mask'
_TAG_LINE = re.compile(r"^\[(style|motif)\]\s*(.+?)\s*$", re.MULTILINE)
#: 'JPEG image, 411,876 bytes — Neo-traditional with realism elements; motif: Jack …'
_SUMMARY = re.compile(r"—\s*(?P<styles>[^;]+);\s*motif:\s*(?P<motifs>[^;]+)")
#: the 16-hex tail shared by art_img_<hex> / art_vid_<hex> and art_upload_<tenant>_<hex>
_HEX_TAIL = re.compile(r"([0-9a-f]{16})$")


def _tags_from_artifact(parsed: str | None, summary: str | None) -> tuple[list[str], list[str]]:
    """(styles, motifs) the VLM actually recorded for this piece. Never invented."""
    styles: list[str] = []
    motifs: list[str] = []
    for kind, value in _TAG_LINE.findall(parsed or ""):
        bucket = styles if kind == "style" else motifs
        for part in value.split(","):
            t = part.strip()
            if t and t not in bucket:
                bucket.append(t)
    if not styles and not motifs:
        m = _SUMMARY.search(summary or "")
        if m:
            for part in re.split(r"[/,]", m.group("styles")):
                t = part.strip()
                if t and t not in styles:
                    styles.append(t)
            for part in m.group("motifs").split(","):
                t = part.strip()
                if t and t not in motifs:
                    motifs.append(t)
    return styles[:12], motifs[:12]


def _hex_tail(value: str) -> str | None:
    m = _HEX_TAIL.search((value or "").strip().lower())
    return m.group(1) if m else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", default=os.environ.get("STUDIO_TENANT_ID", "skindesign"))
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL") or os.environ.get("STUDIO_DSN"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    dsn = args.dsn or "postgresql://scalers:scalers@127.0.0.1:5432/scalers"
    filled = skipped = untagged = 0

    with psycopg.connect(dsn, autocommit=True) as conn:
        by_hex: dict[str, tuple[list[str], list[str]]] = {}
        for aid, parsed, summary in conn.execute(
            "SELECT id, parsed_content, summary FROM context_artifacts WHERE tenant_id=%s",
            (args.tenant,),
        ).fetchall():
            h = _hex_tail(str(aid))
            if not h:
                continue
            styles, motifs = _tags_from_artifact(parsed, summary)
            if styles or motifs:
                by_hex[h] = (styles, motifs)

        rows = conn.execute(
            "SELECT id, content FROM assets WHERE asset_type='studio_artwork'"
        ).fetchall()
        for row_id, content in rows:
            doc = content if isinstance(content, dict) else json.loads(content or "{}")
            asset_id = str(doc.get("asset_id") or doc.get("image_ref") or "")
            h = _hex_tail(asset_id)
            if not h:
                continue
            if doc.get("styles") or doc.get("motifs"):
                skipped += 1  # never overwrite a tag that is already there
                continue
            tags = by_hex.get(h)
            if not tags:
                untagged += 1  # no VLM analysis on file — stays honestly untagged
                continue
            styles, motifs = tags
            doc["styles"], doc["motifs"] = styles, motifs
            print(f"  {asset_id[-18:]}  styles={styles}  motifs={motifs}")
            if not args.dry_run:
                conn.execute(
                    "UPDATE assets SET content=%s WHERE id=%s",
                    (json.dumps(doc), row_id),
                )
            filled += 1

    verb = "would fill" if args.dry_run else "filled"
    print(f"\n{verb} {filled} · already tagged {skipped} · no analysis on file {untagged}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
