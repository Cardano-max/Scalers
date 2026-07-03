"""Skin Design client-data import (CustomerAcq-ju1.1) — deterministic, keyless.

Imports the operator's REAL customers.csv + artists.csv into a hard-sandboxed
tenant. Safe-by-construction and honest:

  * idempotent (re-run = upsert keyed on (tenant, lower(email)); no dupes),
  * explicit missing-markers (this data has NO conversation history / social
    profile / lead stage / artist affinity — recorded, never fabricated),
  * NEVER silently drops a column: unknown CSV columns are reported in the import
    summary (the research-grounding audit CRIT), and the result states exactly
    what was ingested vs skipped,
  * zero LLM calls; the source files are gitignored PII and never leave disk.

Run: ``python -m studio.client_import <client-data-dir> [tenant_id]``
(creates/keeps the tenant row with ``test_mode=TRUE`` — the server-side send gate
in ``actions.publish`` then refuses every real-customer send for it).
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from pathlib import Path
from typing import Any

# The columns the importer KNOWS how to ingest. Anything else in the file is
# reported as unknown (never silently dropped).
CUSTOMER_COLUMNS: tuple[str, ...] = ("name", "email", "phone")
ARTIST_COLUMNS: tuple[str, ...] = ("artist_name", "artist_email", "artist_phone", "studio_name")

# Honest missing-markers for what this dataset does NOT contain.
MISSING_FLAGS: dict[str, str] = {
    "conversation_history": "missing",
    "social_profile": "missing",
    "artist_affinity": "unknown",
}

_PHONE_JUNK = re.compile(r"[\s\-().]")


def normalize_phone(raw: str | None) -> str | None:
    """E.164 or ``None`` — never an empty string and never a fabricated number.

    Accepts already-E.164 values, digit strings with separators, and 10-digit US
    numbers (prefixed ``+1``). ``NULL``/blank/unparseable -> ``None``."""
    v = (raw or "").strip()
    if not v or v.upper() == "NULL":
        return None
    v = _PHONE_JUNK.sub("", v)
    if v.startswith("+"):
        digits = v[1:]
        return f"+{digits}" if digits.isdigit() and 7 <= len(digits) <= 15 else None
    if v.isdigit():
        if len(v) == 10:  # bare US number
            return f"+1{v}"
        if 11 <= len(v) <= 15:
            return f"+{v}"
    return None


def _read_csv(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        return list(reader), list(reader.fieldnames or [])


def parse_customers_csv(path: Path | str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Parse + normalize the customers CSV (PURE — no DB).

    Returns ``(rows, summary)``: trimmed names, lowercased emails, E.164-or-null
    phones, in-file email dedupe (first row wins, duplicates logged), explicit
    missing-markers on every row, and a summary that reports unknown columns and
    per-row skips so nothing is silently dropped."""
    path = Path(path)
    raw_rows, fieldnames = _read_csv(path)
    unknown = [c for c in fieldnames if c not in CUSTOMER_COLUMNS]

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    duplicates: list[str] = []
    skipped: list[dict[str, Any]] = []
    phones_null = 0
    phones_invalid: list[dict[str, Any]] = []

    for i, raw in enumerate(raw_rows, start=2):  # 1-based + header line
        email = (raw.get("email") or "").strip().lower()
        if not email:
            skipped.append({"row": i, "reason": "no email (required upsert key)"})
            continue
        if email in seen:
            duplicates.append(email)
            continue  # first occurrence wins; never double-imported
        seen.add(email)
        raw_phone = (raw.get("phone") or "").strip()
        phone = normalize_phone(raw_phone)
        if phone is None:
            phones_null += 1
            # Honest split: absent in the source vs present-but-not-a-valid-number.
            if raw_phone and raw_phone.upper() != "NULL":
                phones_invalid.append({"row": i, "raw": raw_phone})
        rows.append(
            {
                "name": (raw.get("name") or "").strip() or None,
                "email": email,
                "phone": phone,
                "source_file": path.name,
                "is_test_safe": False,
                "consent_status": "unknown",
                "lead_stage": "unknown",
                "data_flags": dict(MISSING_FLAGS),
            }
        )

    summary = {
        "source_file": path.name,
        "rows_seen": len(raw_rows),
        "parsed": len(rows),
        "duplicates": duplicates,
        "skipped": skipped,
        "phones_null": phones_null,
        "phones_missing": phones_null - len(phones_invalid),
        "phones_invalid": phones_invalid,
        "ingested_columns": list(CUSTOMER_COLUMNS),
        "unknown_columns": unknown,
    }
    return rows, summary


def parse_artists_csv(
    path: Path | str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Parse the artists CSV (PURE): unique artists by normalized (name, email) +
    one studio mapping per source row. ``TEST *`` artists are imported but flagged
    ``is_test`` (quarantined from generation). Placeholders stay EMPTY."""
    path = Path(path)
    raw_rows, fieldnames = _read_csv(path)
    unknown = [c for c in fieldnames if c not in ARTIST_COLUMNS]

    artists: dict[tuple[str, str], dict[str, Any]] = {}
    mappings: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for i, raw in enumerate(raw_rows, start=2):
        name = (raw.get("artist_name") or "").strip()
        email = (raw.get("artist_email") or "").strip().lower()
        studio = (raw.get("studio_name") or "").strip()
        if not name:
            skipped.append({"row": i, "reason": "no artist_name"})
            continue
        key = (name.lower(), email)
        is_test = name.lower().startswith("test ")
        if key not in artists:
            artists[key] = {
                "id": "art_" + hashlib.sha1(f"{key[0]}|{key[1]}".encode()).hexdigest()[:16],
                "name": name,
                "email": email or None,
                "phone": normalize_phone(raw.get("artist_phone")),
                "is_test": is_test,
                # Placeholders: empty, never fabricated (filled by later beads).
                "artist_persona": None,
                "artist_style_tags": None,
                "artist_offer_history": None,
                "artwork_assets": None,
            }
        elif artists[key]["phone"] is None:
            artists[key]["phone"] = normalize_phone(raw.get("artist_phone"))
        mappings.append(
            {
                "artist_id": artists[key]["id"],
                "studio_name": studio,
                "is_test": is_test,
            }
        )

    summary = {
        "source_file": path.name,
        "rows_seen": len(raw_rows),
        "unique_artists": len(artists),
        "mappings": len(mappings),
        "skipped": skipped,
        "ingested_columns": list(ARTIST_COLUMNS),
        "unknown_columns": unknown,
    }
    return list(artists.values()), mappings, summary


# ── persistence (real PG; idempotent) ────────────────────────────────────────── #

_CUSTOMER_EXT_DDL = (
    "ALTER TABLE customers ADD COLUMN IF NOT EXISTS source_file TEXT",
    "ALTER TABLE customers ADD COLUMN IF NOT EXISTS is_test_safe BOOLEAN",
    "ALTER TABLE customers ADD COLUMN IF NOT EXISTS consent_status TEXT",
    "ALTER TABLE customers ADD COLUMN IF NOT EXISTS data_flags JSONB",
    "ALTER TABLE customers ADD COLUMN IF NOT EXISTS phone TEXT",
)

_ARTISTS_DDL = """
CREATE TABLE IF NOT EXISTS artists (
    id                   TEXT PRIMARY KEY,
    tenant_id            TEXT NOT NULL,
    name                 TEXT NOT NULL,
    email                TEXT,
    phone                TEXT,
    is_test              BOOLEAN NOT NULL DEFAULT FALSE,
    artist_persona       TEXT,
    artist_style_tags    JSONB,
    artist_offer_history JSONB,
    artwork_assets       JSONB,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS artist_studios (
    artist_id   TEXT NOT NULL REFERENCES artists(id) ON DELETE CASCADE,
    studio_name TEXT NOT NULL,
    PRIMARY KEY (artist_id, studio_name)
)
"""


def _connect(dsn: str | None):
    import os

    import psycopg
    from psycopg.rows import dict_row

    resolved = (
        dsn
        or os.environ.get("ENGINE_DATABASE_URL")
        or "postgresql://scalers:scalers@localhost:5432/scalers"
    )
    return psycopg.connect(resolved, row_factory=dict_row, autocommit=True)


def import_customers(
    csv_path: Path | str, tenant_id: str, *, dsn: str | None = None
) -> dict[str, Any]:
    """Parse + persist the customers CSV for ``tenant_id`` (idempotent upsert by
    (tenant, lower(email)) via the existing ``upsert_lead``, then the skindesign
    columns in one UPDATE). Returns the parse summary + created/matched counts."""
    from studio.customer_research import upsert_lead
    from tenants.store import get_tenant, upsert_tenant

    # SANDBOX ON IMPORT (wwy.4): importing real customer PII ALWAYS puts the
    # tenant in test_mode — a real-PII tenant can never be born live. Force
    # test_mode=True; preserve any existing display name.
    try:
        _existing = get_tenant(tenant_id, dsn=dsn)
    except Exception:
        _existing = None
    upsert_tenant(
        tenant_id, (_existing or {}).get("name") or tenant_id, test_mode=True, dsn=dsn
    )

    rows, summary = parse_customers_csv(csv_path)
    created = matched = 0
    with _connect(dsn) as conn:
        for ddl in _CUSTOMER_EXT_DDL:
            conn.execute(ddl)
        for r in rows:
            res = upsert_lead(
                tenant_id,
                {"name": r["name"] or "", "email": r["email"], "lead_stage": r["lead_stage"]},
                dsn=dsn,
            )
            created += 1 if res["created"] else 0
            matched += 0 if res["created"] else 1
            conn.execute(
                "UPDATE customers SET phone=%s, source_file=%s, is_test_safe=%s, "
                "consent_status=%s, data_flags=%s::jsonb WHERE id=%s",
                (
                    r["phone"],
                    r["source_file"],
                    r["is_test_safe"],
                    r["consent_status"],
                    json.dumps(r["data_flags"]),
                    res["customer_id"],
                ),
            )
    summary.update({"created": created, "matched": matched, "tenant_id": tenant_id})
    return summary


def import_artists(
    csv_path: Path | str, tenant_id: str, *, dsn: str | None = None
) -> dict[str, Any]:
    """Parse + persist the artists CSV: unique artists + studio mappings, both
    idempotent (ON CONFLICT). TEST artists carry ``is_test=true`` (quarantined)."""
    artists, mappings, summary = parse_artists_csv(csv_path)
    with _connect(dsn) as conn:
        for stmt in _ARTISTS_DDL.split(";"):
            if stmt.strip():
                conn.execute(stmt)
        for a in artists:
            conn.execute(
                "INSERT INTO artists (id, tenant_id, name, email, phone, is_test) "
                "VALUES (%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (id) DO UPDATE SET name=EXCLUDED.name, email=EXCLUDED.email, "
                "phone=COALESCE(artists.phone, EXCLUDED.phone), is_test=EXCLUDED.is_test",
                (a["id"], tenant_id, a["name"], a["email"], a["phone"], a["is_test"]),
            )
        for m in mappings:
            conn.execute(
                "INSERT INTO artist_studios (artist_id, studio_name) VALUES (%s,%s) "
                "ON CONFLICT DO NOTHING",
                (m["artist_id"], m["studio_name"]),
            )
    summary.update({"artists": len(artists), "mappings": len(mappings), "tenant_id": tenant_id})
    return summary


def main(argv: list[str] | None = None) -> int:
    """CLI: import both files + ensure the tenant row (test_mode=TRUE, sandboxed)."""
    import sys

    from tenants.store import upsert_tenant

    args = argv if argv is not None else sys.argv[1:]
    data_dir = Path(args[0]) if args else Path("C:/Users/Links/Desktop/CustomerAcq/client-data")
    tenant = args[1] if len(args) > 1 else "skindesign"

    upsert_tenant(tenant, "Skin Design Tattoo", test_mode=True)
    c = import_customers(data_dir / "customers.csv", tenant)
    a = import_artists(data_dir / "artists.csv", tenant)
    print(json.dumps({"customers": c, "artists": a}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
