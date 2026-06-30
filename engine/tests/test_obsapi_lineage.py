"""Unit tests for the traceability-spine lineage derivation in ``obsapi.repo``.

These are DB-FREE: they drive the pure helpers with a tiny fake connection so the
honest-null + raise-never contract is pinned without a live Postgres. The crux is
that we NEVER fabricate a producing step — an absent/erroring source yields
``(None, None)`` so the UI links to the run and says the exact step is unknown,
rather than pointing at a guessed wrong step.
"""

from __future__ import annotations

from typing import Any

from obsapi import repo


class _FakeResult:
    def __init__(self, row: Any) -> None:
        self._row = row

    def fetchone(self) -> Any:
        return self._row

    def fetchall(self) -> Any:
        return self._row or []


class _FakeConn:
    """Records the last SQL/params and returns a canned row (or raises)."""

    def __init__(self, row: Any = None, raises: bool = False) -> None:
        self._row = row
        self._raises = raises
        self.last_sql: str | None = None
        self.last_params: Any = None

    def execute(self, sql: str, params: Any = ()) -> _FakeResult:
        self.last_sql = sql
        self.last_params = params
        if self._raises:
            raise RuntimeError("boom")
        return _FakeResult(self._row)


# --------------------------------------------------------------------------- #
# _parse_campaign_from_run_id — run_id convention fallback
# --------------------------------------------------------------------------- #
def test_parse_campaign_strips_uuid_suffix():
    assert repo._parse_campaign_from_run_id("team-camp_6521bc-abcdef123456") == "camp_6521bc"


def test_parse_campaign_non_team_is_none():
    assert repo._parse_campaign_from_run_id("run_4821") is None
    assert repo._parse_campaign_from_run_id(None) is None


def test_parse_campaign_no_uuid_suffix_is_none():
    # "team-foo" has no trailing -uuid segment to strip → honest None
    assert repo._parse_campaign_from_run_id("team-foo") is None


# --------------------------------------------------------------------------- #
# _campaign_id_for — agent_runs authoritative, run_id convention fallback
# --------------------------------------------------------------------------- #
def test_campaign_id_prefers_agent_runs_row():
    conn = _FakeConn(row={"campaign_id": "c_authoritative"})
    assert repo._campaign_id_for(conn, "team-other-uuid") == "c_authoritative"


def test_campaign_id_falls_back_to_run_id_convention():
    conn = _FakeConn(row=None)  # agent_runs has no campaign_id
    assert repo._campaign_id_for(conn, "team-camp9-zz1122334455") == "camp9"


def test_campaign_id_none_run_id_short_circuits():
    conn = _FakeConn(row={"campaign_id": "should_not_be_used"})
    assert repo._campaign_id_for(conn, None) is None
    assert conn.last_sql is None  # never queried


def test_campaign_id_raise_never():
    conn = _FakeConn(raises=True)
    # DB hiccup → degrade to the run_id convention parse, not an exception
    assert repo._campaign_id_for(conn, "team-camp9-zz1122334455") == "camp9"


# --------------------------------------------------------------------------- #
# _producing_step_for — the honest step linkage (the crux: never a guessed step)
# --------------------------------------------------------------------------- #
def test_producing_step_returns_id_and_role_on_drafting_match():
    conn = _FakeConn(row={"id": "step_99", "role": "Copywriter"})
    sid, role = repo._producing_step_for(conn, "team-x-uuid")
    assert sid == "step_99"
    assert role == "Copywriter"
    # it filters to the known drafting roles (no wrong-step guessing)
    assert "lower(role) in" in conn.last_sql.lower()
    assert conn.last_params[0] == "team-x-uuid"
    for drafting_role in repo._DRAFTING_ROLES:
        assert drafting_role in conn.last_params


def test_producing_step_none_when_no_drafting_row():
    conn = _FakeConn(row=None)  # no agent_runs row with a drafting role
    assert repo._producing_step_for(conn, "run-1") == (None, None)


def test_producing_step_none_run_id_short_circuits():
    conn = _FakeConn(row={"id": "x", "role": "Copywriter"})
    assert repo._producing_step_for(conn, None) == (None, None)
    assert conn.last_sql is None  # never queried


def test_producing_step_raise_never():
    conn = _FakeConn(raises=True)
    # never crashes the read path — honest (None, None)
    assert repo._producing_step_for(conn, "run-1") == (None, None)


def test_producing_step_coerces_non_str_id_to_str():
    conn = _FakeConn(row={"id": 12345, "role": "draft"})
    sid, role = repo._producing_step_for(conn, "run-1")
    assert sid == "12345"
    assert role == "draft"
