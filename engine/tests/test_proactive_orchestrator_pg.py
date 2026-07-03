"""PG-integration + unit tests for run_daily_scan (CustomerAcq-fr1.1 AC-2/3/4/5).

AC-3 (the 439 invariant): every scheduled proposal stages HELD ('pending'); no
scheduled run can produce a 'sent' action. AC-4: a dead LLM key badges the day
degraded-deterministic-only but still stages. AC-5: phantom channels refused, SMS
only when the gate is importable.

PG tests require a real local Postgres (RUN_PG_TESTS / ENGINE_DATABASE_URL).
"""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta

import psycopg
import pytest

from tests.conftest import private_schema

TENANT = "sdt-test"


# --- unit: channel guard (AC-5) --------------------------------------------

def test_channel_guard_refuses_phantom_channel():
    from proactive.orchestrator import _channel_ok

    refused: list[str] = []
    assert _channel_ok("carrier-pigeon", refused, "k1") is False
    assert refused == ["k1"]
    assert _channel_ok("email", [], "k2") is True


def test_channel_guard_allows_sms_only_when_gate_importable(monkeypatch):
    from proactive.orchestrator import _channel_ok

    assert _channel_ok("sms", [], "k") is True  # gate importable in this env
    # Simulate the gate being absent -> SMS must be refused, never proposed.
    monkeypatch.setitem(sys.modules, "compliance.sms_gate", None)
    refused: list[str] = []
    assert _channel_ok("sms", refused, "k") is False
    assert refused == ["k"]


# --- PG: staging + invariants ----------------------------------------------

pg = pytest.mark.skipif(
    not os.getenv("ENGINE_DATABASE_URL"), reason="requires Postgres"
)


@pytest.fixture()
def scan_env():
    with private_schema("08-actions.sql", "17-actions-archive.sql") as sch:
        yield sch


def _sends(today):
    from proactive.detectors import PriorSend

    return [PriorSend("c@x.com", today - timedelta(days=3), "camp1", name="Cid", spots_remaining=2)]


def _artists():
    from proactive.detectors import ArtistSpecial

    return [ArtistSpecial("nikko", "Nikko", last_special_on=None)]


def _run(env, today, **kw):
    from proactive.orchestrator import run_daily_scan

    return run_daily_scan(TENANT, today, dsn=env.dsn, subdivisions=("NV",), **kw)


@pg
def test_scan_stages_all_three_detectors_held_never_sent(scan_env, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "funded")  # not degraded
    today = date(2026, 7, 1)  # Independence Day + National Tattoo Day in window
    detail = _run(scan_env, today, prior_sends=_sends(today), artists=_artists())

    assert detail["badge"] == "ok" and detail["degraded"] is False
    assert detail["ttl_swept"] is not None  # fr1.3 hygiene tick ran

    with psycopg.connect(scan_env.dsn) as conn:
        rows = conn.execute("SELECT status, type, worker FROM actions").fetchall()
    assert rows, "the scan must stage proposals"
    # 439 INVARIANT: every staged action is HELD; none is sent.
    assert all(r[0] == "pending" for r in rows)
    assert not [r for r in rows if r[0] == "sent"]
    assert all(r[2] == "proactive_scanner" for r in rows)
    kinds = {r[1] for r in rows}
    assert {"holiday", "follow_up", "artist_special"} <= kinds


@pg
def test_scan_is_idempotent_per_fire_date(scan_env, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "funded")
    today = date(2026, 7, 1)
    d1 = _run(scan_env, today, prior_sends=_sends(today), artists=_artists())
    d2 = _run(scan_env, today, prior_sends=_sends(today), artists=_artists())

    assert d1["n_staged"] >= 3
    assert d2["n_staged"] == 0
    assert len(d2["skipped_existing"]) == d1["n_staged"]
    with psycopg.connect(scan_env.dsn) as conn:
        (count,) = conn.execute("SELECT count(*) FROM actions").fetchone()
    assert count == d1["n_staged"]  # re-drive staged no duplicates


@pg
def test_degraded_when_llm_unfunded_still_stages(scan_env, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    today = date(2026, 7, 1)
    detail = _run(scan_env, today)  # holiday-only is enough to prove staging

    assert detail["degraded"] is True
    assert detail["badge"] == "degraded: deterministic-only"
    assert detail["n_staged"] >= 1  # deterministic proposals still staged, not skipped


@pg
def test_scan_excludes_opted_out_followup(scan_env, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "funded")
    today = date(2026, 7, 1)
    detail = _run(
        scan_env, today, prior_sends=_sends(today),
        opted_out=frozenset({"c@x.com"}),
    )
    with psycopg.connect(scan_env.dsn) as conn:
        follow = conn.execute(
            "SELECT count(*) FROM actions WHERE type = 'follow_up'"
        ).fetchone()[0]
    assert follow == 0  # opted-out recipient never gets a follow-up proposal
