"""Bounded 3-level recovery tests (HARN-03 / systemdesign §2.4).

Exercises retry → regenerate → human-review, bounded (no infinite loop), and
that a non-recoverable error propagates instead of being silently retried.
"""

from __future__ import annotations

import pytest

from harness.recovery import (
    RecoverableError,
    RecoveryLevel,
    run_with_recovery,
    run_with_recovery_async,
)


def test_retry_level_succeeds_after_transient_failures():
    state = {"i": 0}

    def step():
        state["i"] += 1
        if state["i"] < 2:
            raise RecoverableError("flaky")
        return "ok"

    result = run_with_recovery(step, max_retries=2)
    assert result.level is RecoveryLevel.RETRY
    assert result.value == "ok"
    assert result.attempts == 2
    assert not result.escalated


def test_regenerate_level_after_retries_exhausted():
    def step():
        raise RecoverableError("always fails")

    def regenerate():
        return "patched"

    result = run_with_recovery(step, max_retries=2, regenerate=regenerate)
    assert result.level is RecoveryLevel.REGENERATE
    assert result.value == "patched"
    assert result.attempts == 4  # 3 step attempts + 1 regenerate
    assert not result.escalated


def test_human_escalation_when_retry_and_regenerate_both_fail():
    def step():
        raise RecoverableError("nope")

    def regenerate():
        raise RecoverableError("also nope")

    result = run_with_recovery(step, max_retries=1, regenerate=regenerate)
    assert result.level is RecoveryLevel.HUMAN_REVIEW
    assert result.value is None
    assert result.escalated


def test_no_regenerate_escalates_after_retries():
    def step():
        raise RecoverableError("nope")

    result = run_with_recovery(step, max_retries=0)
    assert result.level is RecoveryLevel.HUMAN_REVIEW
    assert result.attempts == 1
    assert result.escalated


def test_recovery_is_bounded_no_infinite_loop():
    calls = {"n": 0}

    def step():
        calls["n"] += 1
        raise RecoverableError("x")

    run_with_recovery(step, max_retries=5)
    assert calls["n"] == 6  # exactly max_retries + 1, then it stops


def test_non_recoverable_error_propagates():
    def step():
        raise ValueError("hard fault — not recoverable")

    with pytest.raises(ValueError):
        run_with_recovery(step, max_retries=3)


async def test_async_recovery_walks_levels():
    async def step():
        raise RecoverableError("x")

    async def regenerate():
        return "ok"

    result = await run_with_recovery_async(step, max_retries=1, regenerate=regenerate)
    assert result.level is RecoveryLevel.REGENERATE
    assert result.value == "ok"
