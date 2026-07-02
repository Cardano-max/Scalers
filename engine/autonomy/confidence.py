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

# Canonical temp for the self-consistency PROBE (ADR Decision 2: "a temp>0 probe,
# separate from the temp-0 decision path"). The decision sample stays temp-0/pinned;
# only the probe samples vary, so determinism of the shipped output is untouched.
PROBE_TEMPERATURE = 0.7

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

    Use this for a pool of PEER samples (all probes, no privileged member). When one
    sample is the artifact that actually SHIPS, use :func:`anchored_self_consistency`
    instead — modal agreement can rate a pool highly even when every probe disagrees
    with the shipped sample.
    """
    n = len(signatures)
    if n < min_samples:
        return None
    modal = Counter(signatures).most_common(1)[0][1]
    return modal / n


# Minimum PROBE samples (excluding the anchor) for an anchored estimate.
MIN_PROBES = 2


def anchored_self_consistency(
    anchor: Any, probe_signatures: Sequence[Any], *, min_probes: int = MIN_PROBES
) -> float | None:
    """Agreement of temp>0 probe samples WITH the shipped (anchor) sample, ``[0,1]``.

    ``= (probes matching the anchor) / (total probes)``. The anchor does NOT vote
    for itself — a confidence that describes the shipped artifact must be measured
    against it, not against whatever the probes happened to cluster on (adversarial
    finding: with modal scoring, K−1 probes agreeing with EACH OTHER while all
    contradicting the shipped draft still read high). Fewer than ``min_probes``
    surviving probes → ``None`` (fail-safe). NO logprobs.
    """
    if len(probe_signatures) < min_probes:
        return None
    matches = sum(1 for s in probe_signatures if s == anchor)
    return matches / len(probe_signatures)


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
    honest (ECE≤0.05 on the real gold set, rvy.8).

    A bin the gold set never measured maps to ``None`` and ``apply`` passes the raw
    value through UNCHANGED there — "no data for this region" must never round a
    confidence UP to the bin edge (adversarial finding: gold pairs only at the
    extremes turned a below-bar 0.81 into an above-bar 0.9)."""

    bins: tuple[tuple[float, float | None], ...] = ()  # (upper_edge, accuracy | None=unmeasured)

    def apply(self, x: float) -> float:
        x = max(0.0, min(1.0, x))
        for upper, acc in self.bins:
            if x <= upper:
                return x if acc is None else acc  # unmeasured bin -> identity
        return x  # identity outside any fitted bin (incl. the default empty map)

    @classmethod
    def fit(cls, pairs: Sequence[tuple[float, bool]], *, n_bins: int = 10) -> "Calibration":
        """Histogram-bin calibration: each bin maps to the empirical accuracy of the
        raw confidences that fell in it (the canonical reliability-diagram remap).
        Bins with no gold data stay ``None`` (identity on apply), never an assumed
        accuracy."""
        if not pairs:
            return cls()
        buckets: list[list[bool]] = [[] for _ in range(n_bins)]
        for conf, correct in pairs:
            idx = min(n_bins - 1, max(0, int(max(0.0, min(1.0, conf)) * n_bins)))
            buckets[idx].append(correct)
        bins: list[tuple[float, float | None]] = []
        for i, b in enumerate(buckets):
            upper = (i + 1) / n_bins
            acc = sum(b) / len(b) if b else None  # empty bin -> unmeasured, NOT an edge
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

    ``confidence = min(calibrate(w_q·jury_quality + w_c·self_consistency),
    jury_quality, self_consistency)`` — the ADR's weighted mean, **capped at the
    weakest component**. The cap is what makes the pool genuinely conservative
    ("both must be high to trust an auto-fire", ADR Decision 2): a weighted mean
    alone lets a stable-but-mediocre generator (high self-consistency, below-bar
    jury) or a good-but-wobbly one (high jury, low self-consistency) blend its way
    over the threshold — a confidence component must only ever BLOCK an auto-fire,
    never lift another component past the bar. (Formula-vs-intent contradiction in
    the ADR resolved in the strictly-blocking direction; flagged to arch on 4jx.3.)

    ``self_consistency_score is None`` (uncomputable) → ``uncomputable=True`` and
    ``confidence=None`` so the decision layer fails safe.
    """
    if self_consistency_score is None:
        return ConfidenceResult(
            confidence=None, jury_quality=jury_quality, self_consistency=None, uncomputable=True
        )
    raw = (w_q * jury_quality + w_c * self_consistency_score) / (w_q + w_c)
    calibrated = max(0.0, min(1.0, calibration.apply(raw)))
    # Weakest-component cap: calibration may correct the blend up or down, but the
    # final confidence never exceeds either input signal.
    capped = min(calibrated, jury_quality, self_consistency_score)
    return ConfidenceResult(
        confidence=capped,
        jury_quality=jury_quality,
        self_consistency=self_consistency_score,
        components={
            "raw": raw,
            "calibrated": calibrated,
            "jury_quality": jury_quality,
            "self_consistency": self_consistency_score,
        },
    )


def ece(pairs: Sequence[tuple[float, bool]], **kw):
    """Expected Calibration Error over ``(confidence, correct)`` pairs (rvy.8 metric),
    the gate that goes live on real computed confidence here. Reuses the eval metric so
    the gating ECE and the computed-confidence ECE are the SAME computation."""
    return expected_calibration_error(pairs, **kw)
