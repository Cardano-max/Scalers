"""Hard per-run budget cap for the research adapter (bead a9m.2, contract §3).

A run must never rack up unbounded API spend or hang. The :class:`Budget` caps
**calls**, **credits** (Foreplay/Exa), and **wall-time** per run; the
:class:`BudgetMeter` is checked BEFORE each paid provider call and accumulated
after. When any limit would be exceeded the adapter stops calling and returns
what it has with ``over_budget=True`` — it never blocks, hangs, or overspends.

Deterministic: wall-time uses an injected ``now`` (monotonic seconds), like
``research.safety.RateLimiter``, so tests don't depend on the wall clock.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Budget:
    """Per-run ceiling. A non-positive field means "unlimited on that axis"."""

    max_calls: int = 0          # 0 -> unlimited calls
    max_credits: float = 0.0    # 0 -> unlimited credits
    max_wall_seconds: float = 0.0  # 0 -> unlimited wall-time

    @classmethod
    def unlimited(cls) -> "Budget":
        return cls()


class BudgetMeter:
    """Tracks spend against a :class:`Budget`. Pre-check with :meth:`can_afford`,
    record with :meth:`charge`; :meth:`exhausted` ends the run early."""

    def __init__(self, budget: Budget, *, start: float = 0.0) -> None:
        self._b = budget
        self._start = start
        self._now = start
        self.calls = 0
        self.credits = 0.0

    def tick(self, now: float) -> None:
        """Advance the run clock (monotonic seconds)."""
        self._now = now

    @property
    def elapsed(self) -> float:
        return max(0.0, self._now - self._start)

    def _over(self, calls: int, credits: float) -> bool:
        b = self._b
        if b.max_calls > 0 and calls > b.max_calls:
            return True
        if b.max_credits > 0 and credits > b.max_credits:
            return True
        if b.max_wall_seconds > 0 and self.elapsed > b.max_wall_seconds:
            return True
        return False

    def can_afford(self, *, calls: int = 1, credits: float = 0.0) -> bool:
        """True if charging ``calls``/``credits`` now stays within budget."""
        return not self._over(self.calls + calls, self.credits + credits)

    def charge(self, *, calls: int = 1, credits: float = 0.0) -> None:
        self.calls += calls
        self.credits += credits

    @property
    def exhausted(self) -> bool:
        """True once any axis is at/over its limit (wall-time included)."""
        return self._over(self.calls, self.credits) or (
            self._b.max_wall_seconds > 0 and self.elapsed >= self._b.max_wall_seconds
        )
