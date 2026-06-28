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

    # A failed deterministic gate means the artifact is broken — re-draft it
    # (checked first, so a HELD tenant's broken content still regenerates rather
    # than going to a human as-is; the regenerate/escalate distinction is kept).
    if gates and any(not gate.passed for gate in gates):
        return RouteDecision.REGENERATE

    # bead-439 (CustomerAcq-b3f): a HELD tenant/channel never auto-fires. HOLD
    # forces human review, overriding confidence and the dial — so no signal
    # (incl. the stubbed jury's hardcoded 0.9 confidence) can route it to AUTO.
    # Ordering is safe either way: the only path to AUTO is the final return,
    # which HOLD short-circuits.
    if autonomy is AutonomyMode.HOLD:
        return RouteDecision.REVIEW

    if confidence < threshold:
        return RouteDecision.REVIEW
    if autonomy is AutonomyMode.REVIEW:
        return RouteDecision.REVIEW
    return RouteDecision.AUTO
