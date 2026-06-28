"""Tests for valid-rate reporting (cells.metrics)."""

from __future__ import annotations

from cells.metrics import ValidRateReport


def test_empty_report_is_zero():
    r = ValidRateReport()
    assert r.first_pass_rate == 0.0
    assert r.after_retry_rate == 0.0


def test_rates_and_rescue_accounting():
    r = ValidRateReport(label="content_brief")
    r.record(valid=True, first_pass=True)            # clean
    r.record(valid=True, first_pass=False, repairs=1)  # rescued by repair
    r.record(valid=True, first_pass=False, repairs=2)  # rescued by repair
    r.record(valid=False, first_pass=False, repairs=2)  # failed on a code path

    assert r.total == 4
    assert r.first_pass_rate == 0.25       # 1/4 clean first pass
    assert r.after_retry_rate == 0.75      # 3/4 ultimately valid
    assert r.repair_rescued == 2           # two saved by the repair loop
    assert r.failed == 1
    assert "first-pass=25.0%" in r.render()
    assert "after-retry=75.0%" in r.render()
