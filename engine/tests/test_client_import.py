"""Skin Design client-data import — CustomerAcq-ju1.1.

1,093 REAL customers + 71 artist-studio rows land in the hard-sandboxed
``skindesign`` tenant. The importer is deterministic/keyless, idempotent (re-run =
upsert, no dupes), honest about what the data does NOT contain (explicit
missing-markers), and NEVER silently drops a column (unknown CSV columns are
reported in the summary — the audit CRIT).

Unit lane: pure parsing over synthetic CSVs (always runs in CI) + the REAL files
when present (client-data/ is gitignored PII — tests skip cleanly without it).
Integration lane: persistence on real PG (idempotency, ladies8391 untouched).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from studio.client_import import (
    CUSTOMER_COLUMNS,
    normalize_phone,
    parse_artists_csv,
    parse_customers_csv,
)

CLIENT_DATA = Path("C:/Users/Links/Desktop/CustomerAcq/client-data")
REAL_CUSTOMERS = CLIENT_DATA / "customers.csv"
REAL_ARTISTS = CLIENT_DATA / "artists.csv"

needs_real_files = pytest.mark.skipif(
    not REAL_CUSTOMERS.exists(), reason="client-data/ (gitignored PII) not present"
)


# ── phone normalization: E.164 or null, never "" ────────────────────────────── #


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("+16154852458", "+16154852458"),
        ("(615) 485-2458", "+16154852458"),  # 10-digit US -> +1
        ("615-485-2458", "+16154852458"),
        ("16154852458", "+16154852458"),
        ("", None),
        ("   ", None),
        ("NULL", None),
        ("null", None),
        ("not-a-phone", None),
        ("123", None),  # too short to be a real number
    ],
)
def test_normalize_phone(raw, expected):
    assert normalize_phone(raw) == expected


# ── customers: parsing, trimming, dedupe, unknown columns ───────────────────── #


def _write_csv(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_parse_customers_trims_dedupes_and_reports(tmp_path):
    p = _write_csv(
        tmp_path,
        "customers.csv",
        "name,email,phone,secret_notes\n"
        "Angel  ,ANGEL@Example.com,+16150000001,vip\n"
        " Cielo,cielo@example.com,NULL,\n"
        "Angel Dup,angel@example.com,+16150000002,\n"  # duplicate email
        "NoPhone,nophone@example.com,,\n",
    )
    rows, summary = parse_customers_csv(p)

    # trimmed names, lowercased emails
    by_email = {r["email"]: r for r in rows}
    assert by_email["angel@example.com"]["name"] == "Angel"
    assert by_email["cielo@example.com"]["name"] == "Cielo"
    # duplicate email -> ONE row kept, logged (never double-imported)
    assert len(rows) == 3
    assert summary["duplicates"] == ["angel@example.com"]
    # NULL / empty phones -> None, never ""
    assert by_email["cielo@example.com"]["phone"] is None
    assert by_email["nophone@example.com"]["phone"] is None
    assert summary["phones_null"] == 2
    # audit CRIT: the unknown column is REPORTED, not silently dropped
    assert summary["unknown_columns"] == ["secret_notes"]
    assert sorted(summary["ingested_columns"]) == sorted(CUSTOMER_COLUMNS)
    assert summary["rows_seen"] == 4


def test_parse_customers_row_without_email_is_skipped_with_reason(tmp_path):
    p = _write_csv(tmp_path, "c.csv", "name,email,phone\nNoEmail,,+16150000003\n")
    rows, summary = parse_customers_csv(p)
    assert rows == []
    assert summary["skipped"] and "email" in summary["skipped"][0]["reason"].lower()


def test_parsed_customer_carries_honest_missing_markers(tmp_path):
    p = _write_csv(tmp_path, "c.csv", "name,email,phone\nA,a@example.com,\n")
    rows, _ = parse_customers_csv(p)
    r = rows[0]
    assert r["is_test_safe"] is False
    assert r["consent_status"] == "unknown"
    assert r["lead_stage"] == "unknown"
    assert r["data_flags"] == {
        "conversation_history": "missing",
        "social_profile": "missing",
        "artist_affinity": "unknown",
    }
    assert r["source_file"] == "c.csv"


@needs_real_files
def test_real_customers_csv_shape():
    rows, summary = parse_customers_csv(REAL_CUSTOMERS)
    assert summary["rows_seen"] == 1093  # EXACTLY the expected source rows
    # 30 phones missing in the source + 1 present-but-invalid ("(909) 767-835",
    # 9 digits) -> nulled per "E.164 or null", reported honestly, never kept malformed.
    assert summary["phones_missing"] == 30
    assert len(summary["phones_invalid"]) == 1
    assert summary["phones_null"] == 31
    assert summary["duplicates"] == ["melissa90660@gmail.com"]
    assert len(rows) == 1092  # 1093 - 1 duplicate
    assert summary["unknown_columns"] == []  # name/email/phone all ingested
    # spot: whitespace-dirty names arrive trimmed
    assert all(r["name"] == (r["name"] or "").strip() for r in rows)


# ── artists: 71 rows -> 37 unique + 71 mappings, TEST rows quarantined ──────── #


def test_parse_artists_unifies_by_name_email_and_flags_test(tmp_path):
    p = _write_csv(
        tmp_path,
        "artists.csv",
        "artist_name,artist_email,artist_phone,studio_name\n"
        "Maya Ink,maya@example.com,+16150000009,Studio A\n"
        "Maya Ink,maya@example.com,,Studio B\n"  # same artist, 2nd studio
        "TEST jacob artist,jacob@test.example,NULL,Studio A\n",
    )
    artists, mappings, summary = parse_artists_csv(p)

    assert len(artists) == 2  # Maya once, TEST once
    assert len(mappings) == 3  # every row = one mapping
    maya = next(a for a in artists if a["name"] == "Maya Ink")
    assert maya["phone"] == "+16150000009"  # first real value wins
    test = next(a for a in artists if a["name"].lower().startswith("test "))
    assert test["is_test"] is True  # quarantined from generation
    assert test["phone"] is None
    # placeholders exist and are EMPTY (never fabricated)
    for field in ("artist_persona", "artist_style_tags", "artist_offer_history", "artwork_assets"):
        assert maya[field] is None
    assert summary["rows_seen"] == 3


@needs_real_files
def test_real_artists_csv_shape():
    artists, mappings, summary = parse_artists_csv(REAL_ARTISTS)
    assert summary["rows_seen"] == 71
    assert len(artists) == 37  # unique by normalized name+email
    assert len(mappings) == 71  # every source row mapped
    assert len({m["studio_name"] for m in mappings}) == 6
    test_rows = [m for m in mappings if m["is_test"]]
    assert len(test_rows) == 2  # both TEST source rows flagged
    assert summary["unknown_columns"] == []


# ── persistence on real PG: idempotent, sandboxed, ladies8391 untouched ─────── #


@pytest.mark.integration
def test_import_persists_idempotently_and_leaves_ladies8391_untouched(tmp_path):
    import psycopg
    from psycopg.rows import dict_row

    from studio.client_import import import_artists, import_customers
    from tenants.store import ensure_schema as ensure_tenants, get_tenant, upsert_tenant

    dsn = (
        os.environ.get("ENGINE_DATABASE_URL")
        or "postgresql://scalers:scalers@localhost:5432/scalers"
    )
    tenant = "t_sdtest"

    cust_csv = _write_csv(
        tmp_path,
        "customers.csv",
        "name,email,phone\nAngel  ,angel@example.com,+16150000001\n"
        "Cielo,cielo@example.com,NULL\nAngel Dup,angel@example.com,+16150000002\n",
    )
    art_csv = _write_csv(
        tmp_path,
        "artists.csv",
        "artist_name,artist_email,artist_phone,studio_name\n"
        "Maya Ink,maya@example.com,+16150000009,Studio A\n"
        "Maya Ink,maya@example.com,,Studio B\n"
        "TEST jacob artist,jacob@test.example,NULL,Studio A\n",
    )

    conn = psycopg.connect(dsn, row_factory=dict_row, autocommit=True)
    ladies_before = conn.execute(
        "SELECT count(*) n FROM customers WHERE tenant_id='ladies8391'"
    ).fetchone()["n"]

    try:
        ensure_tenants(dsn)
        upsert_tenant(tenant, "SD Import Test", test_mode=True, dsn=dsn)
        assert get_tenant(tenant, dsn=dsn)["test_mode"] is True

        r1 = import_customers(cust_csv, tenant, dsn=dsn)
        r2 = import_customers(cust_csv, tenant, dsn=dsn)  # re-run: upsert, no dupes
        assert r1["created"] == 2 and r1["matched"] == 0
        assert r2["created"] == 0 and r2["matched"] == 2

        n = conn.execute(
            "SELECT count(*) n FROM customers WHERE tenant_id=%s", (tenant,)
        ).fetchone()["n"]
        assert n == 2
        row = conn.execute(
            "SELECT name, phone, source_file, is_test_safe, consent_status, lead_stage "
            "FROM customers WHERE tenant_id=%s AND email='angel@example.com'",
            (tenant,),
        ).fetchone()
        assert row["name"] == "Angel"  # trimmed
        assert row["phone"] == "+16150000001"  # first value kept on dupe
        assert row["source_file"] == "customers.csv"
        assert row["is_test_safe"] is False
        assert row["consent_status"] == "unknown"
        assert row["lead_stage"] == "unknown"
        null_phone = conn.execute(
            "SELECT phone FROM customers WHERE tenant_id=%s AND email='cielo@example.com'",
            (tenant,),
        ).fetchone()["phone"]
        assert null_phone is None  # null, not ""

        a1 = import_artists(art_csv, tenant, dsn=dsn)
        a2 = import_artists(art_csv, tenant, dsn=dsn)  # idempotent
        assert a1["artists"] == 2 and a1["mappings"] == 3
        assert a2["artists"] == 2 and a2["mappings"] == 3
        n_art = conn.execute(
            "SELECT count(*) n FROM artists WHERE tenant_id=%s", (tenant,)
        ).fetchone()["n"]
        n_map = conn.execute(
            "SELECT count(*) n FROM artist_studios WHERE artist_id IN "
            "(SELECT id FROM artists WHERE tenant_id=%s)",
            (tenant,),
        ).fetchone()["n"]
        assert n_art == 2 and n_map == 3
        quarantined = conn.execute(
            "SELECT is_test FROM artists WHERE tenant_id=%s AND lower(name) LIKE 'test %%'",
            (tenant,),
        ).fetchone()["is_test"]
        assert quarantined is True

        ladies_after = conn.execute(
            "SELECT count(*) n FROM customers WHERE tenant_id='ladies8391'"
        ).fetchone()["n"]
        assert ladies_after == ladies_before  # completely unaffected
    finally:
        conn.execute(
            "DELETE FROM artist_studios WHERE artist_id IN (SELECT id FROM artists WHERE tenant_id=%s)",
            (tenant,),
        )
        conn.execute("DELETE FROM artists WHERE tenant_id=%s", (tenant,))
        conn.execute("DELETE FROM customers WHERE tenant_id=%s", (tenant,))
        conn.execute("DELETE FROM tenants WHERE id=%s", (tenant,))
