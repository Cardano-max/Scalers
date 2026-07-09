#!/usr/bin/env python3
"""Bulk-ingest a folder of REAL artist artwork into the engine — one command.

    python3 scripts/ingest_artwork_dir.py <folder> <artist-name> [--engine http://localhost:8000] [--prompt "..."]

For every .png/.jpg/.jpeg/.webp in <folder> it POSTs /studio/upload/image with
the artist link and an optional shared operator prompt. The engine stores the
bytes on disk, runs the real VLM analysis (style / motif / color / mood /
campaign fit), adds the piece to the artist's selectable artwork library, and
writes an artist-memory entry — the same path as the console's Upload artwork
button, just batched.

Honest output per file: uploaded/failed + the VLM status the engine reported.
Nothing is ever published; artwork only becomes available for draft attachment
and top-4 selection.
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import sys
import urllib.request
from pathlib import Path

EXTS = {".png", ".jpg", ".jpeg", ".webp"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("artist")
    ap.add_argument("--engine", default="http://localhost:8000")
    ap.add_argument("--prompt", default="")
    args = ap.parse_args()

    folder = Path(args.folder)
    files = sorted(p for p in folder.iterdir() if p.suffix.lower() in EXTS) if folder.is_dir() else []
    if not files:
        print(f"no images (.png/.jpg/.jpeg/.webp) found in {folder}")
        return 1
    print(f"ingesting {len(files)} images for artist {args.artist!r} via {args.engine}")

    ok = failed = 0
    for p in files:
        mime = mimetypes.guess_type(p.name)[0] or "image/png"
        payload = {
            "name": p.name,
            "contentBase64": f"data:{mime};base64," + base64.b64encode(p.read_bytes()).decode(),
            "artist": args.artist,
            "prompt": args.prompt,
        }
        req = urllib.request.Request(
            f"{args.engine}/studio/upload/image",
            data=json.dumps(payload).encode(),
            headers={"content-type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                d = json.loads(r.read())
            ok += 1
            print(f"  OK  {p.name}  vlm={d.get('vlmStatus')}  {str(d.get('vlmSummary') or '')[:80]}")
        except Exception as exc:  # noqa: BLE001 — per-file honesty, keep going
            failed += 1
            print(f"  FAIL {p.name}: {exc}")
    print(f"done: {ok} uploaded, {failed} failed. Open the console's Artists tab -> {args.artist} to see the gallery.")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
