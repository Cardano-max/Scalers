#!/usr/bin/env python3
"""Prune portfolio artwork rows whose image bytes are GONE — the "preview
unavailable + no style/motif tags" gallery cards.

A real operator's Artists gallery showed a whole row of dead cards: asset rows
whose linked artifact either no longer exists or points at a storage_path that
is not on THIS machine's disk (uploads made in another environment, or test
uploads whose files were cleaned). The console renders those as
"preview unavailable" with no tags — they can never recover on their own,
because the bytes are simply not there.

DRY-RUN by default: prints a per-row report and writes nothing until --fix.

Classes:
  OK                  — artifact row + file on disk (left alone, always)
  UNTAGGED            — bytes exist but no VLM tags (left alone; re-upload the
                        same file to analyze it — the ingest is idempotent)
  ORPHAN_NO_ARTIFACT  — asset points at an artifact row that does not exist
  ORPHAN_FILE_MISSING — artifact row exists but its file is not on this disk

--fix deletes ONLY the two ORPHAN classes (asset row + the dead artifact row
for FILE_MISSING). Customers, conversations, memories and tagged artwork are
never touched.

Usage:
  python scripts/prune_broken_artworks.py --tenant skindesign
  python scripts/prune_broken_artworks.py --tenant skindesign --fix

DSN comes from ENGINE_DATABASE_URL (else the local default).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parent.parent / "engine"
sys.path.insert(0, str(ENGINE_DIR))

_DEFAULT_DSN = "postgresql://scalers:scalers@localhost:5432/scalers"


def _connect(dsn: str | None):
    import psycopg
    from psycopg.rows import dict_row

    conninfo = dsn or os.environ.get("ENGINE_DATABASE_URL", _DEFAULT_DSN)
    return psycopg.connect(conninfo, row_factory=dict_row, autocommit=True)


def classify_artworks(tenant_id: str, *, dsn: str | None = None) -> list[dict]:
    """One report row per portfolio asset: id, artist, caption, class, path."""
    campaign_id = f"portfolio:{tenant_id}"
    with _connect(dsn) as conn:
        assets = conn.execute(
            "SELECT id, content FROM assets WHERE campaign_id = %s ORDER BY created_at",
            (campaign_id,),
        ).fetchall()
        report: list[dict] = []
        for a in assets:
            content = a["content"]
            if isinstance(content, str):
                try:
                    content = json.loads(content)
                except Exception:
                    content = {}
            content = content if isinstance(content, dict) else {}
            image_ref = str(content.get("image_ref") or "")
            artifact_id = (
                image_ref.replace("artifact://", "")
                if image_ref.startswith("artifact://")
                else str(content.get("artifact_id") or "")
            )
            tagged = bool(content.get("styles") or content.get("motifs"))
            row = {
                "asset_id": a["id"],
                "artist": content.get("artist"),
                "caption": (str(content.get("caption") or ""))[:60],
                "artifact_id": artifact_id or None,
                "path": None,
                "class": "OK",
            }
            if not artifact_id:
                # No artifact linkage at all — only prunable when ALSO untagged
                # (a tagged legacy row without bytes still carries real analysis).
                row["class"] = "ORPHAN_NO_ARTIFACT" if not tagged else "OK"
                report.append(row)
                continue
            art = conn.execute(
                "SELECT id, meta FROM context_artifacts WHERE id = %s", (artifact_id,)
            ).fetchone()
            if art is None:
                row["class"] = "ORPHAN_NO_ARTIFACT"
                report.append(row)
                continue
            meta = art["meta"]
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except Exception:
                    meta = {}
            meta = meta if isinstance(meta, dict) else {}
            path = str(meta.get("storage_path") or "")
            row["path"] = path or None
            if not path or not Path(path).is_file():
                row["class"] = "ORPHAN_FILE_MISSING"
            elif not tagged:
                row["class"] = "UNTAGGED"
            report.append(row)
        return report


def prune(tenant_id: str, *, dsn: str | None = None, fix: bool = False) -> dict:
    report = classify_artworks(tenant_id, dsn=dsn)
    counts: dict[str, int] = {}
    for r in report:
        counts[r["class"]] = counts.get(r["class"], 0) + 1

    deleted_assets = 0
    deleted_artifacts = 0
    if fix:
        with _connect(dsn) as conn:
            for r in report:
                if r["class"] not in ("ORPHAN_NO_ARTIFACT", "ORPHAN_FILE_MISSING"):
                    continue
                conn.execute("DELETE FROM assets WHERE id = %s", (r["asset_id"],))
                deleted_assets += 1
                if r["class"] == "ORPHAN_FILE_MISSING" and r["artifact_id"]:
                    conn.execute(
                        "DELETE FROM context_artifacts WHERE id = %s", (r["artifact_id"],)
                    )
                    deleted_artifacts += 1
    return {
        "report": report,
        "counts": counts,
        "deleted_assets": deleted_assets,
        "deleted_artifacts": deleted_artifacts,
        "fixed": fix,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Report/delete portfolio artwork rows whose image bytes are gone."
    )
    p.add_argument("--tenant", required=True, help="Tenant id, e.g. skindesign.")
    p.add_argument("--dsn", default=None, help="Postgres DSN (default: ENGINE_DATABASE_URL).")
    p.add_argument("--fix", action="store_true",
                   help="Delete the ORPHAN rows (default: report only).")
    args = p.parse_args(argv)

    out = prune(args.tenant, dsn=args.dsn, fix=args.fix)
    for r in out["report"]:
        if r["class"] == "OK":
            continue
        print(f"{r['class']:<20} {r['asset_id']}  artist={r['artist']!r}  "
              f"caption={r['caption']!r}  path={r['path']}")
    print(f"\ncounts: {out['counts']}")
    if args.fix:
        print(f"deleted: {out['deleted_assets']} asset row(s), "
              f"{out['deleted_artifacts']} dead artifact row(s)")
    else:
        orphans = (out["counts"].get("ORPHAN_NO_ARTIFACT", 0)
                   + out["counts"].get("ORPHAN_FILE_MISSING", 0))
        if orphans:
            print(f"DRY RUN — re-run with --fix to delete the {orphans} orphan row(s).")
        else:
            print("Nothing to prune — every gallery card has real bytes behind it.")
        untagged = out["counts"].get("UNTAGGED", 0)
        if untagged:
            print(f"{untagged} row(s) have bytes but no VLM tags — re-upload the same "
                  "file (idempotent) with the engine's model key set to analyze them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
