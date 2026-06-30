"""Studio customers-CSV upload parse — hermetic, no DB, no network.

Proves the /studio/upload parse helper is a REAL parse of the uploaded bytes
(honest row count + columns + sample) and that it never claims ingestion.
"""

from __future__ import annotations

import pytest

from studio.agui import parse_customers_csv


def test_parses_rows_columns_and_sample() -> None:
    csv = "name,email,city\nAda,ada@x.io,London\nGrace,grace@y.io,NYC\nLin,lin@z.io,Berlin\n"
    out = parse_customers_csv(csv, "customers.csv")
    assert out["ok"] is True
    assert out["filename"] == "customers.csv"
    assert out["columns"] == ["name", "email", "city"]
    assert out["rows"] == 3  # data rows, header excluded
    assert out["sample"][0] == {"name": "Ada", "email": "ada@x.io", "city": "London"}
    # honesty: parsed only, never ingested by this endpoint
    assert out["ingested"] is False


def test_sample_is_capped_at_five_rows() -> None:
    body = "id\n" + "\n".join(str(i) for i in range(20)) + "\n"
    out = parse_customers_csv(body)
    assert out["rows"] == 20
    assert len(out["sample"]) == 5


def test_strips_bom_and_blank_lines() -> None:
    csv = "﻿name,email\n\nBob,bob@x.io\n\n"
    out = parse_customers_csv(csv)
    assert out["columns"] == ["name", "email"]  # BOM stripped from first header cell
    assert out["rows"] == 1


def test_ragged_row_gets_synthetic_column_key() -> None:
    csv = "a,b\n1,2,3\n"
    out = parse_customers_csv(csv)
    assert out["sample"][0] == {"a": "1", "b": "2", "col3": "3"}


def test_empty_content_raises() -> None:
    with pytest.raises(ValueError):
        parse_customers_csv("   \n  \n")
