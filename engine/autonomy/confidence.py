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

import math
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


def _pool_adjacent_violators(values: list[float], weights: list[float]) -> list[float]:
    """Weighted isotonic regression (PAV): the closest nondecreasing sequence to
    ``values`` under the given weights. Adjacent violating blocks are pooled to
    their weighted mean — the canonical monotonization for a reliability remap."""
    # each block: [mean, weight, n_items]
    blocks: list[list[float]] = []
    for v, w in zip(values, weights):
        blocks.append([v, w, 1])
        while len(blocks) > 1 and blocks[-2][0] > blocks[-1][0]:
            v2, w2, n2 = blocks.pop()
            v1, w1, n1 = blocks.pop()
            blocks.append([(v1 * w1 + v2 * w2) / (w1 + w2), w1 + w2, n1 + n2])
    out: list[float] = []
    for mean, _, n in blocks:
        out.extend([mean] * int(n))
    return out


@dataclass(frozen=True)
class Calibration:
    """A monotone confidence calibration map. Identity by default; ``fit`` builds a
    binned map from ``(raw_confidence, correct)`` pairs so the routed confidence is
    honest (ECE≤0.05 on the real gold set, rvy.8).

    A bin the gold set never measured maps to ``None`` and ``apply`` passes the raw
    value through UNCHANGED there — "no data for this region" must never round a
    confidence UP to the bin edge (adversarial finding: gold pairs only at the
    extremes turned a below-bar 0.81 into an above-bar 0.9). But identity is **not
    AUTO-eligible** (4jx.15, ADR "Unmeasured calibration bins"): a raw landing in an
    unmeasured bin at/above the channel threshold is UNCOMPUTABLE — an evidence-free
    region must never be more permissive than a measured one. See
    :meth:`unmeasured_at_or_above` (enforced in :func:`compute_confidence`).

    ``apply`` is monotone nondecreasing over the whole domain (the declared
    contract): ``fit`` monotonizes measured bins with PAV and reconciles the
    measured/identity boundaries by only ever LOWERING a value (``caps``), never
    raising one — conservative by construction."""

    bins: tuple[tuple[float, float | None], ...] = ()  # (upper_edge, accuracy | None=unmeasured)
    # Per-bin ceiling for unmeasured (identity) bins: the infimum of the map over
    # the bins above, so identity can never sit ABOVE later measured evidence.
    # Empty = uncapped (1.0 everywhere), the pre-4jx.15 shape.
    caps: tuple[float, ...] = ()

    def apply(self, x: float) -> float:
        x = max(0.0, min(1.0, x))
        if not self.bins:
            return x  # identity default (empty map)
        # Index EXACTLY as fit() buckets (left-inclusive, last bin closed): a value
        # on a bin edge (e.g. 0.9) must land in the same bin it was fitted into.
        # The previous right-inclusive scan (x <= upper) mapped edge values one bin
        # LOW, silently dropping their fitted correction (rvy.8 QA finding).
        idx = min(len(self.bins) - 1, int(x * len(self.bins)))
        acc = self.bins[idx][1]
        if acc is not None:
            return acc
        # unmeasured bin -> identity (never rounds up), capped so the whole map
        # stays monotone against measured evidence above (4jx.15).
        return min(x, self.caps[idx]) if self.caps else x

    def is_measured(self, x: float) -> bool:
        """True when ``x`` lands in a bin fitted from at least one gold pair.
        False on the empty (unfitted) map — there is no measurement anywhere."""
        if not self.bins:
            return False
        idx = min(len(self.bins) - 1, int(max(0.0, min(1.0, x)) * len(self.bins)))
        return self.bins[idx][1] is not None

    def unmeasured_at_or_above(self, x: float, threshold: float) -> bool:
        """The 4jx.15 routing predicate: ``x`` lands in an unmeasured bin of a
        FITTED map at/above the channel threshold — identity would clear the bar
        with zero calibration evidence, so the caller must treat the confidence
        as uncomputable (→ review). The wholly-unfitted identity default is exempt:
        that is the flagged pre-gold-set state (module docstring), not a map with
        a blind spot the ECE gate can miss."""
        return bool(self.bins) and x >= threshold and not self.is_measured(x)

    @classmethod
    def fit(cls, pairs: Sequence[tuple[float, bool]], *, n_bins: int = 10) -> "Calibration":
        """Histogram-bin calibration: each bin maps to the empirical accuracy of the
        raw confidences that fell in it (the canonical reliability-diagram remap).
        Bins with no gold data stay ``None`` (identity on apply), never an assumed
        accuracy.

        Monotonization (4jx.15): measured accuracies are PAV-pooled (count-weighted)
        so weaker evidence never maps more favorably, then reconciled with the
        identity bins right-to-left so the WHOLE map is monotone nondecreasing —
        every adjustment only ever lowers a value (a measured bin is capped at the
        infimum of the map above it; identity bins get the same cap), never raises
        one."""
        if not pairs:
            return cls()
        buckets: list[list[bool]] = [[] for _ in range(n_bins)]
        for conf, correct in pairs:
            idx = min(n_bins - 1, max(0, int(max(0.0, min(1.0, conf)) * n_bins)))
            buckets[idx].append(correct)
        accs: list[float | None] = [
            sum(b) / len(b) if b else None for b in buckets  # empty -> unmeasured, NOT an edge
        ]
        # 1) PAV over the measured subsequence, weighted by gold count per bin.
        measured_idx = [i for i, a in enumerate(accs) if a is not None]
        pooled = _pool_adjacent_violators(
            [accs[i] for i in measured_idx], [float(len(buckets[i])) for i in measured_idx]
        )
        for i, v in zip(measured_idx, pooled):
            accs[i] = v
        # 2) Boundary reconciliation, right-to-left: `inf_above` is the infimum of
        #    the final map over all bins strictly above the current one. A measured
        #    bin is lowered to it if needed; an unmeasured bin stores it as its
        #    identity cap (its own infimum is then its lower edge, capped).
        caps = [1.0] * n_bins
        inf_above = 1.0
        for i in range(n_bins - 1, -1, -1):
            if accs[i] is not None:
                accs[i] = min(accs[i], inf_above)
                inf_above = accs[i]
            else:
                caps[i] = inf_above
                inf_above = min(i / n_bins, inf_above)
        bins = tuple(((i + 1) / n_bins, accs[i]) for i in range(n_bins))
        return cls(bins=bins, caps=tuple(caps))


IDENTITY_CALIBRATION = Calibration()


@dataclass(frozen=True)
class ConfidenceResult:
    """The computed confidence + its components (persisted/audited)."""

    confidence: float | None      # None == uncomputable -> fail safe to review
    jury_quality: float
    self_consistency: float | None
    uncomputable: bool = False
    # WHY it was uncomputable, for the audit trail ("" = the default probe-starvation
    # reason; the decision layer labels the escalation with it when present).
    uncomputable_reason: str = ""
    components: dict[str, float] = field(default_factory=dict)


def compute_confidence(
    *,
    jury_quality: float,
    self_consistency_score: float | None,
    w_q: float = W_JURY,
    w_c: float = W_SELF_CONSISTENCY,
    calibration: Calibration = IDENTITY_CALIBRATION,
    threshold: float | None = None,
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

    When the caller supplies the channel ``threshold`` (the live decision path
    does), a pooled raw landing in an UNMEASURED bin of a fitted map at/above that
    threshold is also uncomputable (4jx.15, ADR "Unmeasured calibration bins"):
    identity there would clear the bar with zero calibration evidence — and
    selection dynamics make the top bins precisely the likely-unmeasured ones.
    Below-threshold unmeasured bins keep identity (observability; they route to
    review through the ordinary below-threshold comparison). The eval/measurement
    lane passes no threshold — it must be able to MEASURE unmeasured bins.
    """
    if self_consistency_score is None:
        return ConfidenceResult(
            confidence=None, jury_quality=jury_quality, self_consistency=None, uncomputable=True
        )
    # Finite-input guards (4jx.14, ADR D2-as-amended #93: all inputs finite in
    # [0,1]; non-finite -> uncomputable -> review). Python's min() ignores NaN in
    # non-first position, so a single NaN/inf input would LAUNDER through the
    # min-cap to confidence 1.0 -> AUTO (panel-verified exploit). Never route a
    # number we cannot trust.
    if (
        not math.isfinite(jury_quality)
        or not math.isfinite(self_consistency_score)
        or not math.isfinite(w_q)
        or not math.isfinite(w_c)
        or (w_q + w_c) <= 0.0
    ):
        return ConfidenceResult(
            confidence=None,
            jury_quality=jury_quality,
            self_consistency=self_consistency_score,
            uncomputable=True,
        )
    raw = (w_q * jury_quality + w_c * self_consistency_score) / (w_q + w_c)
    # 4jx.15: an evidence-free calibration region at/above the bar is not a
    # confidence, it is the ABSENCE of one. Checked on the raw (identity would
    # route it), before calibration/cap can dress it up as a number.
    if threshold is not None and calibration.unmeasured_at_or_above(raw, threshold):
        return ConfidenceResult(
            confidence=None,
            jury_quality=jury_quality,
            self_consistency=self_consistency_score,
            uncomputable=True,
            uncomputable_reason=(
                f"unmeasured calibration bin (raw {raw:.2f} >= threshold {threshold:.2f})"
            ),
        )
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
