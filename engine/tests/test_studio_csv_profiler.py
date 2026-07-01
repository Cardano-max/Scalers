"""CSV semantic profiler tests (P1-A) — pure/offline (no DB, no model).

Proves the profiler is HONEST:
* it classifies columns into marketing roles by header + value heuristics;
* it counts REAL segments / objections / social presence over the rows;
* an unknown column is surfaced by name, never mapped to a fabricated role;
* an absent dimension (no segment/objection/social column) is reported absent, and the
  natural summary states real counts that match the file — nothing invented.
"""

from __future__ import annotations

from studio.agui import parse_customers_csv
from studio.csv_profiler import build_profile


def _rows(cols: list[str], data: list[list[str]]) -> list[dict[str, str]]:
    return [{cols[i]: r[i] for i in range(len(cols))} for r in data]


# --------------------------------------------------------------------------- #
# Rich CSV — exercises segment / objection / social classification + counts.
# --------------------------------------------------------------------------- #
_RICH_COLS = ["name", "email", "status", "instagram", "objection", "mystery"]
_RICH_DATA = [
    ["Ada", "ada@x.io", "warm lead", "@ada.ink", "price too expensive", "x1"],
    ["Grace", "grace@y.io", "warm", "@grace", "", "x2"],
    ["Lin", "lin@z.io", "cold prospect", "", "still deciding", "x3"],
    ["Mo", "mo@z.io", "recurring regular", "instagram.com/mo", "", "x4"],
    ["Sam", "", "past lapsed", "", "no time, too busy", "x5"],
]


def test_classifies_columns_into_roles_and_flags_unknown() -> None:
    profile = build_profile(_RICH_COLS, _rows(_RICH_COLS, _RICH_DATA))
    roles = profile.column_roles
    assert roles["email"] == "email"
    assert roles["status"] == "customer_type"
    assert roles["instagram"] == "social"
    assert roles["objection"] == "objection"
    assert roles["name"] == "name"
    # a column with no known signal AND no value pattern is surfaced honestly, not guessed
    assert "mystery" in profile.unknown_columns
    assert "mystery" not in roles


def test_counts_real_segments_objections_and_social() -> None:
    profile = build_profile(_RICH_COLS, _rows(_RICH_COLS, _RICH_DATA))
    assert profile.total_leads == 5
    # segments from the status column (warm x2, cold x1, recurring x1, past x1)
    assert profile.segments.get("warm") == 2
    assert profile.segments.get("cold") == 1
    assert profile.segments.get("recurring") == 1
    assert profile.segments.get("past") == 1
    assert profile.segments_source == "column"
    # objections from the dedicated objection column (price, uncertainty, timing)
    assert profile.objections.get("price") == 1
    assert profile.objections.get("uncertainty") == 1
    assert profile.objections.get("timing") == 1
    assert profile.objections_source == "column"
    # social: 3 rows have a non-empty instagram cell
    assert profile.social_present == 3
    assert profile.social_source == "column"
    # emails: 4 of 5 rows have an email
    assert profile.emails_present == 4


def test_summary_states_real_counts() -> None:
    profile = build_profile(_RICH_COLS, _rows(_RICH_COLS, _RICH_DATA))
    s = profile.summary_text
    assert "I found 5 leads" in s
    assert "2 warm" in s
    assert "price objection" in s
    assert "3 with an Instagram/social handle" in s
    assert "Personalize one message per lead?" in s


# --------------------------------------------------------------------------- #
# Value-based rescue — an unlabeled column classified from its values.
# --------------------------------------------------------------------------- #
def test_value_heuristic_rescues_email_and_social_columns() -> None:
    cols = ["contact_col", "handle_col"]
    data = [
        ["a@b.io", "@one"],
        ["c@d.io", "@two"],
        ["e@f.io", "instagram.com/three"],
    ]
    profile = build_profile(cols, _rows(cols, data))
    # "contact_col" has no email header signal but its VALUES are emails -> email role
    assert profile.column_roles.get("contact_col") == "email"
    assert profile.emails_present == 3
    # "handle_col" values are @handles -> social
    assert profile.column_roles.get("handle_col") == "social"
    assert profile.social_present == 3


# --------------------------------------------------------------------------- #
# Honest absence — the REAL repo CSV shape (name,email,city,notes): no segment,
# objection, or social column. The profiler must report those absent, not invent them.
# --------------------------------------------------------------------------- #
_REAL_CSV = (
    "name,email,city,notes\n"
    "World Tattoo Studio,worldtattoostudio@gmail.com,\"Denver, CO\",Primary contact for walk-ins\n"
    "La Emme Tattoo Studio,laemmestudios@gmail.com,USA,Official email for inquiries\n"
    "Inside Ink Tattoo Studio,insideinktattoostudio@gmail.com,\"Largo, FL\",Direct business address\n"
)


def test_real_repo_shape_reports_absent_dimensions_without_fabrication() -> None:
    out = parse_customers_csv(_REAL_CSV, "tattoo-studio-leads.csv")
    prof = out["profile"]
    assert prof["total_leads"] == 3
    assert prof["column_roles"]["email"] == "email"
    assert prof["column_roles"]["city"] == "location"
    assert prof["column_roles"]["notes"] == "notes"
    # NO segment / objection / social column -> all absent, nothing invented
    assert prof["segments"] == {}
    assert prof["objections"] == {}
    assert prof["social_present"] == 0
    # the honest summary states the real lead count + names the absences
    summary = out["summary"]
    assert "I found 3 leads" in summary
    assert "I don't see" in summary
    assert "Every one has an email address" in summary


def test_empty_rows_summary_is_honest() -> None:
    profile = build_profile(["name"], [])
    assert profile.total_leads == 0
    assert "no lead rows" in profile.summary_text


def test_parse_customers_csv_attaches_profile_and_summary() -> None:
    out = parse_customers_csv("name,email\nAda,ada@x.io\n", "c.csv")
    assert "profile" in out and "summary" in out
    assert out["profile"]["total_leads"] == 1
    assert "I found 1 lead" in out["summary"]
