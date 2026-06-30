"""Uploaded-CSV -> supervisor context tests — pure/offline (no DB, no network).

Proves the fix for "the supervisor can't read my CSV": an uploaded customer list
(a REAL parse stored on the plan by /studio/upload) is surfaced to the supervisor on
every turn via `_customers_context` AND summarized into the run brief for the draft
agents. Honesty: with NO CSV the supervisor sees nothing (it must not pretend a list
exists), and only the real parsed sample rows are shown — never invented ones.
"""

from __future__ import annotations

from types import SimpleNamespace

from studio.agui import CampaignPlan, _brief_from_plan, _customers_context


def _ctx(plan: CampaignPlan):
    return SimpleNamespace(deps=SimpleNamespace(state=plan))


def _uploaded() -> dict:
    return {
        "filename": "leads.csv",
        "rows": 10,
        "columns": ["name", "email", "city", "notes"],
        "sample": [
            {"name": "Ada", "email": "ada@x.io", "city": "London", "notes": "fine-line fan"},
            {"name": "Grace", "email": "grace@y.io", "city": "NYC", "notes": ""},
        ],
        "ingested": True,
    }


def test_uploaded_csv_surfaces_to_the_supervisor() -> None:
    plan = CampaignPlan(goal="win back", customers=_uploaded())
    rendered = _customers_context(_ctx(plan))  # type: ignore[arg-type]
    assert "UPLOADED CUSTOMER LIST" in rendered
    assert "rows: 10" in rendered
    assert "name, email, city, notes" in rendered
    # a real sample row appears verbatim — the supervisor can read the rows
    assert "Ada" in rendered and "ada@x.io" in rendered
    # it points the supervisor at the grounded, HELD per-lead path
    assert "research_and_stage_leads" in rendered


def test_run_brief_carries_a_real_customer_summary() -> None:
    plan = CampaignPlan(goal="win back", customers=_uploaded())
    brief = _brief_from_plan(plan)
    assert "Uploaded customer list: 10 row(s)" in brief
    assert "name, email, city, notes" in brief


def test_no_csv_means_the_supervisor_sees_nothing() -> None:
    # HONESTY: no upload -> empty context (no fabrication) and no brief line.
    empty = CampaignPlan(goal="win back")
    assert _customers_context(_ctx(empty)) == ""  # type: ignore[arg-type]
    assert "Uploaded customer list" not in _brief_from_plan(empty)
    # a zero-row parse is also treated as "no list" (never a fake acknowledgement)
    zero = CampaignPlan(goal="g", customers={"rows": 0, "columns": [], "sample": []})
    assert _customers_context(_ctx(zero)) == ""  # type: ignore[arg-type]
