"""Bounded 3-level recovery (HARN-03, systemdesign §2.4).

Every cell / side-effect runs under bounded recovery: **retry → regenerate /
local-patch → human-review**. After N bounded retries a step escalates rather
than looping forever. Recovery levels are *code, not model judgment*.

This is a thin, synchronous helper. The same shape applies to async steps via
:func:`run_with_recovery_async`. A persistent ``RecoverableError`` walks the
levels; any other exception propagates immediately (it is not a recoverable
fault and must not be silently retried).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Generic, TypeVar

_T = TypeVar("_T")


class RecoveryLevel(str, Enum):
    """Which recovery level produced the outcome."""

    RETRY = "retry"
    REGENERATE = "regenerate"
    HUMAN_REVIEW = "human-review"


class RecoverableError(RuntimeError):
    """A transient/repairable fault that may be retried or regenerated."""


@dataclass(frozen=True)
class RecoveryResult(Generic[_T]):
    """Outcome of a bounded-recovery run.

    ``escalated`` is True only when both retries and regeneration were exhausted
    and the step routed to human review (``value`` is then ``None``).
    """

    level: RecoveryLevel
    value: _T | None
    attempts: int
    escalated: bool


def run_with_recovery(
    step: Callable[[], _T],
    *,
    max_retries: int = 2,
    regenerate: Callable[[], _T] | None = None,
) -> RecoveryResult[_T]:
    """Run ``step`` under bounded recovery; return how it resolved.

    Order: up to ``max_retries + 1`` attempts of ``step`` (RETRY) → one
    ``regenerate`` attempt if provided (REGENERATE) → escalate (HUMAN_REVIEW).
    Bounded: the loop runs a fixed number of times and never spins.
    """

    attempts = 0
    for _ in range(max_retries + 1):
        attempts += 1
        try:
            return RecoveryResult(RecoveryLevel.RETRY, step(), attempts, False)
        except RecoverableError:
            continue

    if regenerate is not None:
        attempts += 1
        try:
            return RecoveryResult(
                RecoveryLevel.REGENERATE, regenerate(), attempts, False
            )
        except RecoverableError:
            pass

    return RecoveryResult(RecoveryLevel.HUMAN_REVIEW, None, attempts, True)


async def run_with_recovery_async(
    step: Callable[[], Awaitable[_T]],
    *,
    max_retries: int = 2,
    regenerate: Callable[[], Awaitable[_T]] | None = None,
) -> RecoveryResult[_T]:
    """Async variant of :func:`run_with_recovery` (same level order and bounds)."""

    attempts = 0
    for _ in range(max_retries + 1):
        attempts += 1
        try:
            return RecoveryResult(RecoveryLevel.RETRY, await step(), attempts, False)
        except RecoverableError:
            continue

    if regenerate is not None:
        attempts += 1
        try:
            return RecoveryResult(
                RecoveryLevel.REGENERATE, await regenerate(), attempts, False
            )
        except RecoverableError:
            pass

    return RecoveryResult(RecoveryLevel.HUMAN_REVIEW, None, attempts, True)
