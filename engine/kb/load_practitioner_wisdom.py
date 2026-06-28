"""CLI: load the practitioner-wisdom JSONL into the GLOBAL grounding partition.

Offline, idempotent loader (run after the rvy.2 KB tables + 04-grounding-kb.sql
exist). Re-running loads the same harvest with no duplicates.

  python -m kb.load_practitioner_wisdom --dsn postgresql://... \
      --jsonl kb/corpus/practitioner_wisdom.jsonl

DSN falls back to ENGINE_DATABASE_URL / SCALERS_TEST_DSN. With ``--embedder
semantic`` a real local model can be wired later; the default deterministic
embedder keeps the load hermetic (matches the eval store).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from kb.grounding import GroundingStore

_DEFAULT_JSONL = Path(__file__).resolve().parent / "corpus" / "practitioner_wisdom.jsonl"


def _read_jsonl(path: Path) -> list[dict]:
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Load practitioner-wisdom into the grounding KB.")
    ap.add_argument(
        "--dsn",
        default=os.environ.get("ENGINE_DATABASE_URL") or os.environ.get("SCALERS_TEST_DSN"),
        help="Postgres DSN (default: $ENGINE_DATABASE_URL / $SCALERS_TEST_DSN)",
    )
    ap.add_argument("--jsonl", type=Path, default=_DEFAULT_JSONL)
    ap.add_argument("--dry-run", action="store_true", help="parse + count only; no DB writes")
    args = ap.parse_args(argv)

    if not args.jsonl.exists():
        print(f"missing JSONL: {args.jsonl} (run build_practitioner_wisdom first)", file=sys.stderr)
        return 2
    entries = _read_jsonl(args.jsonl)

    by_cat: dict[str, int] = {}
    for e in entries:
        by_cat[e["category"]] = by_cat.get(e["category"], 0) + 1
    summary = ", ".join(f"{k}={v}" for k, v in sorted(by_cat.items()))
    print(f"{len(entries)} entries to load ({summary})")

    if args.dry_run:
        return 0
    if not args.dsn:
        print("no DSN (set --dsn or ENGINE_DATABASE_URL)", file=sys.stderr)
        return 2

    store = GroundingStore(args.dsn)
    loaded = store.load_entries(entries)
    total = store.count()
    print(f"loaded {loaded} entries; partition now holds {total} rows (idempotent)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
