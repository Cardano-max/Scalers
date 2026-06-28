"""Pure-code router (HARN-05) — systemdesign §6.2 signature.

``route(confidence, threshold, gates, autonomy)`` is the harness's control
valve. It is a *pure function* of concrete signals — a computed confidence
score, a single auto-bar threshold, the deterministic gate results, and the
channel's autonomy mode — with no model call and no hidden state. It is
arithmetic; temp-0 is irrelevant because there is no model in the loop.

Decision order (first match wins):

1. any gate failed                  -> ``regenerate`` (deterministic reject; re-run).
2. ``confidence < threshold``       -> ``review`` (below the auto bar).
3. autonomy is ``REVIEW``           -> ``review`` (dial forces sign-off).
4. otherwise                        -> ``auto``.

Regenerate is gate-driven: a failed deterministic gate (banned phrase, claim,
length, voice) means the artifact is broken and should be re-generated rather
than sent to a human. Confidence vs the threshold splits auto from review.
"""

from __future__ import annotations

from collections.abc import Sequence

from .state import AutonomyMode, Gate, RouteDecision

# Single auto-bar threshold (inclusive): at or above this, clean output may
# auto-fire. Below it, a human reviews.
DEFAULT_THRESHOLD = 0.85


def route(
    confidence: float,
    threshold: float = DEFAULT_THRESHOLD,
    gates: Sequence[Gate] | None = None,
    autonomy: AutonomyMode = AutonomyMode.AUTO,
) -> RouteDecision:
    """Return the routing decision (``auto`` / ``review`` / ``regenerate``).

    Args:
        confidence: Computed confidence in ``[0.0, 1.0]``.
        threshold: The auto bar, in ``[0.0, 1.0]`` (inclusive).
        gates: Deterministic gate results; any failure forces ``regenerate``.
        autonomy: The channel's autonomy dial.

    Raises:
        ValueError: If ``confidence`` or ``threshold`` is outside ``[0.0, 1.0]``.
    """

    if not 0.0 <= confidence <= 1.0:
        raise ValueError(f"confidence must be in [0.0, 1.0]; got {confidence!r}")
    if not 0.0 <= threshold <= 1.0:
        raise ValueError(f"threshold must be in [0.0, 1.0]; got {threshold!r}")

    if gates and any(not gate.passed for gate in gates):
        return RouteDecision.REGENERATE
    if confidence < threshold:
        return RouteDecision.REVIEW
    if autonomy is AutonomyMode.REVIEW:
        return RouteDecision.REVIEW
    return RouteDecision.AUTO
