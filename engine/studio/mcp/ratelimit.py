"""Per-principal rate limiting — the spec's "Servers MUST rate limit" control.

The MCP spec (2025-11-25, *server/tools* §"Security Considerations") lists rate
limiting alongside input validation, access control, and output sanitization as
a server MUST. This module provides a small, in-process, per-principal cap.

SCOPE (deliberately honest): this is a *defense-in-depth* cap that bounds how
fast one principal can hammer the tool surface within a single server process —
it is not a distributed quota. Cross-process / global QPS shaping, burst
smoothing, and abuse analytics belong to the transport / gateway layer in front
of the server; this cap is the last-line, always-on floor so a runaway or
compromised principal cannot spin the read path unbounded even with no gateway.

:class:`SlidingWindowRateLimiter` keys on ``"{subject}:{tenant_id}"`` (so the
limit is per calling identity per tenant) and allows ``max_calls`` within any
``window_s`` sliding window. :class:`NullRateLimiter` is the explicit "disabled"
choice. Both are thread-safe, matching the server's threaded execution.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Callable, Protocol, runtime_checkable

from studio.mcp.errors import RateLimitedError


@runtime_checkable
class RateLimiter(Protocol):
    """Checks (and records) one call for ``key``; raises RateLimitedError if over."""

    def check(self, key: str) -> None:
        ...


class NullRateLimiter:
    """A no-op limiter — the explicit way to disable rate limiting."""

    def check(self, key: str) -> None:  # noqa: D401 - trivial
        return None


class SlidingWindowRateLimiter:
    """Allow at most ``max_calls`` per ``key`` within any ``window_s`` window.

    Keeps a per-key deque of recent call timestamps, evicting those older than the
    window on each check. Thread-safe. ``clock`` is injectable for deterministic
    tests (defaults to a monotonic clock so it is immune to wall-clock jumps)."""

    def __init__(
        self,
        max_calls: int,
        window_s: float,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_calls < 1:
            raise ValueError("max_calls must be >= 1")
        if window_s <= 0:
            raise ValueError("window_s must be > 0")
        self.max_calls = int(max_calls)
        self.window_s = float(window_s)
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        now = self._clock()
        cutoff = now - self.window_s
        with self._lock:
            dq = self._hits.setdefault(key, deque())
            while dq and dq[0] <= cutoff:
                dq.popleft()
            if len(dq) >= self.max_calls:
                raise RateLimitedError(
                    f"rate limit exceeded for {key!r}: {self.max_calls} calls per "
                    f"{self.window_s:g}s"
                )
            dq.append(now)


__all__ = ["RateLimiter", "NullRateLimiter", "SlidingWindowRateLimiter"]
