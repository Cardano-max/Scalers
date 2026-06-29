"""Computed confidence (AUTON-02 / 4jx.3, ADR Phase-5 Decision 2).

Replaces the hardcoded ``0.9`` confidence with a real, calibrated signal. Hosted
Claude exposes **no logprobs**, so generation confidence is **self-consistency
variance**: sample the producing cell K times with a temp>0 *probe* (separate from
the temp-0 decision path) and score how much the typed outputs agree. That is pooled
**conservatively** with the jury's quality so a wobbly generator pulls confidence
down even if the jury liked the single sample it judged.

Pure + deterministic given its inputs. The calibration map is fit on the real
per-channel gold set (the rvy.8 ECE≤0.05 gate goes live on real confidence here);
until that gold set exists the map is identity and ECE is *reported*, not gated —
flagged, like the other Phase-5 real-data hooks.

**Fail-safe:** too few samples to estimate self-consistency → ``None`` (uncomputable)
→ the decision layer routes to review. "Couldn't compute" is never treated as high
confidence.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from evals.metrics import expected_calibration_error

# Minimum probe samples before a self-consistency estimate is trustworthy. Below
# this we return None (fail-safe) rather than a confident number from noise.
MIN_SAMPLES = 3
DEFAULT_K = 5

# Pooling weights (ADR Decision 2): jury_quality vs self_consistency. Fit on the gold
# set (rvy.8); uniform until then. Both must be high to trust an auto-fire.
W_JURY = 0.5
W_SELF_CONSISTENCY = 0.5


def self_consistency(signatures: Sequence[Any], *, min_samples: int = MIN_SAMPLES) -> float | None:
    """Modal-agreement self-consistency over K probe-sample signatures, in ``[0,1]``.

    Each signature is a comparable reduction of one probe sample (e.g. the draft's
    normalized text, or a category). ``= (count of the most common signature) / K`` —
    unanimous samples → 1.0; maximally divergent → ``1/K``. Fewer than ``min_samples``
    → ``None`` (fail-safe: don't emit a confident number from noise). NO logprobs.
    """
    n = len(signatures)
    if n < min_samples:
        return None
    modal = Counter(signatures).most_common(1)[0][1]
    return modal / n


async def probe_self_consistency(
    sampler: Callable[[], Awaitable[Any]],
    *,
    k: int = DEFAULT_K,
    min_samples: int = MIN_SAMPLES,
    signature: Callable[[Any], Any] = lambda x: x,
) -> float | None:
    """Run the temp>0 ``sampler`` ``k`` times and score self-consistency over the
    reduced signatures. A sampler call that errors is dropped (not a fake agreement);
    if fewer than ``min_samples`` succeed, returns ``None`` (uncomputable → review).
    ``sampler`` is injectable so the probe is deterministic under test."""
    sigs: list[Any] = []
    for _ in range(k):
        try:
            sigs.append(signature(await sampler()))
        except Exception:  # noqa: BLE001 — a flaky probe sample is dropped, never fatal
            continue
    return self_consistency(sigs, min_samples=min_samples)


@dataclass(frozen=True)
class Calibration:
    """A monotone confidence calibration map. Identity by default; ``fit`` builds a
    binned map from ``(raw_confidence, correct)`` pairs so the routed confidence is
    honest (ECE≤0.05 on the real gold set, rvy.8)."""

    bins: tuple[tuple[float, float], ...] = ()  # (upper_edge, empirical_accuracy)

    def apply(self, x: float) -> float:
        x = max(0.0, min(1.0, x))
        for upper, acc in self.bins:
            if x <= upper:
                return acc
        return x  # identity outside any fitted bin (incl. the default empty map)

    @classmethod
    def fit(cls, pairs: Sequence[tuple[float, bool]], *, n_bins: int = 10) -> "Calibration":
        """Histogram-bin calibration: each bin maps to the empirical accuracy of the
        raw confidences that fell in it (the canonical reliability-diagram remap)."""
        if not pairs:
            return cls()
        buckets: list[list[bool]] = [[] for _ in range(n_bins)]
        for conf, correct in pairs:
            idx = min(n_bins - 1, max(0, int(max(0.0, min(1.0, conf)) * n_bins)))
            buckets[idx].append(correct)
        bins: list[tuple[float, float]] = []
        for i, b in enumerate(buckets):
            upper = (i + 1) / n_bins
            acc = sum(b) / len(b) if b else upper  # empty bin -> identity at its edge
            bins.append((upper, acc))
        return cls(bins=tuple(bins))


IDENTITY_CALIBRATION = Calibration()


@dataclass(frozen=True)
class ConfidenceResult:
    """The computed confidence + its components (persisted/audited)."""

    confidence: float | None      # None == uncomputable -> fail safe to review
    jury_quality: float
    self_consistency: float | None
    uncomputable: bool = False
    components: dict[str, float] = field(default_factory=dict)


def compute_confidence(
    *,
    jury_quality: float,
    self_consistency_score: float | None,
    w_q: float = W_JURY,
    w_c: float = W_SELF_CONSISTENCY,
    calibration: Calibration = IDENTITY_CALIBRATION,
) -> ConfidenceResult:
    """Pool jury quality with self-consistency into one calibrated confidence.

    ``confidence = calibrate(w_q·jury_quality + w_c·self_consistency)`` — CONSERVATIVE:
    a low self-consistency drags the pooled value down even if ``jury_quality`` is
    high (the two are distinct: *is it good?* vs *is the generator stable?*; both must
    be high to auto-fire). ``self_consistency_score is None`` (uncomputable) →
    ``uncomputable=True`` and ``confidence=None`` so the decision layer fails safe.
    """
    if self_consistency_score is None:
        return ConfidenceResult(
            confidence=None, jury_quality=jury_quality, self_consistency=None, uncomputable=True
        )
    raw = (w_q * jury_quality + w_c * self_consistency_score) / (w_q + w_c)
    calibrated = max(0.0, min(1.0, calibration.apply(raw)))
    return ConfidenceResult(
        confidence=calibrated,
        jury_quality=jury_quality,
        self_consistency=self_consistency_score,
        components={"raw": raw, "jury_quality": jury_quality, "self_consistency": self_consistency_score},
    )


def ece(pairs: Sequence[tuple[float, bool]], **kw):
    """Expected Calibration Error over ``(confidence, correct)`` pairs (rvy.8 metric),
    the gate that goes live on real computed confidence here. Reuses the eval metric so
    the gating ECE and the computed-confidence ECE are the SAME computation."""
    return expected_calibration_error(pairs, **kw)
