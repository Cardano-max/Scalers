"""Calibration + accuracy metric math for the eval gates (EVAL-03 / rvy.8).

These are the deterministic, hermetic computations behind the Phase-2 gates:

* **ECE** — Expected Calibration Error (binned), the calibration target.
* **precision / recall / F1** — per classification/extraction cell accuracy.
* **Cohen's kappa** + **on-voice %** — the brand-voice holdout metrics.

Why hermetic in-repo (not DeepEval at the gate): the per-commit gate must be
deterministic and fast (rvy.7's ≤10-min offline budget) and the ADR (rvy.1) makes
the ``eval_metric`` store the authoritative gating source of truth — DeepEval /
Langfuse are non-gating tooling. The formulas here match DeepEval's definitions;
an optional DeepEval backend (``pip install -e '.[eval]'``) can recompute the same
numbers for the periodic/trend lane without changing what the gate decides.

All functions are pure: lists in, :class:`MetricResult` out. ``reliable`` /
``n`` let the gate layer turn "insufficient / unreliable" into *not-promotable*
rather than a misleading pass.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Hashable, Sequence


@dataclass(frozen=True)
class MetricResult:
    """One computed metric value plus the context the gate needs to judge it.

    ``reliable`` is False when the sample is too small or degenerate to trust
    (e.g. ECE over a single confidence bin); the gate maps that to
    *not-promotable*, never a silent pass. ``detail`` carries per-class / per-bin
    breakdowns for the console + debugging.
    """

    name: str
    value: float
    n: int
    reliable: bool = True
    detail: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Calibration — Expected Calibration Error
# --------------------------------------------------------------------------- #


def expected_calibration_error(
    pairs: Sequence[tuple[float, bool]],
    *,
    n_bins: int = 10,
    min_samples: int = 20,
    min_confidence_spread: float = 0.1,
) -> MetricResult:
    """Binned ECE over ``(confidence, correct)`` pairs.

    ECE = Σ_b (n_b / N) · |accuracy_b − mean_confidence_b| across ``n_bins`` equal-
    width bins on ``[0, 1]``. Lower is better; the gate is ``ECE ≤ threshold``.

    ``reliable`` is False (→ not-promotable) when N < ``min_samples`` or every
    confidence falls in too narrow a range (``max−min < min_confidence_spread``),
    since a trivially-narrow range yields a meaningless near-zero ECE.

    Confidence input is the **self-consistency variance** signal (stack-decision:
    hosted Claude exposes no logprobs). In Phase 2 no bead emits per-example
    confidence yet (the producer is AUTON-02, Phase 5), so this runs on
    synthetic/recorded confidence — WIRED now, MEASURED Phase 5.
    """
    n = len(pairs)
    if n == 0:
        return MetricResult("ece", 0.0, 0, reliable=False, detail={"reason": "no data"})

    for c, _ in pairs:
        if not 0.0 <= c <= 1.0:
            raise ValueError(f"confidence must be in [0,1]; got {c!r}")

    confs = [c for c, _ in pairs]
    spread = max(confs) - min(confs)
    reliable = n >= min_samples and spread >= min_confidence_spread

    # Equal-width bins; a confidence of exactly 1.0 lands in the last bin.
    bins: list[list[tuple[float, bool]]] = [[] for _ in range(n_bins)]
    for c, ok in pairs:
        idx = min(int(c * n_bins), n_bins - 1)
        bins[idx].append((c, ok))

    ece = 0.0
    bin_detail = []
    for b in bins:
        if not b:
            continue
        acc = sum(1 for _, ok in b if ok) / len(b)
        conf = sum(c for c, _ in b) / len(b)
        ece += (len(b) / n) * abs(acc - conf)
        bin_detail.append({"n": len(b), "acc": round(acc, 4), "conf": round(conf, 4)})

    detail: dict[str, Any] = {"n_bins": n_bins, "confidence_spread": round(spread, 4), "bins": bin_detail}
    if not reliable:
        detail["reason"] = (
            f"insufficient sample (n={n}<{min_samples})"
            if n < min_samples
            else f"confidence range too narrow (spread={spread:.3f}<{min_confidence_spread})"
        )
    return MetricResult("ece", ece, n, reliable=reliable, detail=detail)


# --------------------------------------------------------------------------- #
# Accuracy — precision / recall / F1
# --------------------------------------------------------------------------- #


def _prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return precision, recall, f1


def classification_prf(
    pairs: Sequence[tuple[Hashable, Hashable]],
    *,
    min_samples: int = 10,
    average: str = "macro",
) -> MetricResult:
    """Macro (default) precision/recall/F1 over ``(predicted, expected)`` labels.

    Macro-averages per-class P/R/F1 so a dominant class can't mask a weak one —
    the right shape for a triage classifier. ``value`` is the macro-F1; per-class
    and macro precision/recall live in ``detail`` so the gate can threshold P and
    R independently (``P/R ≥ 0.95``).
    """
    n = len(pairs)
    if n == 0:
        return MetricResult("classification_f1", 0.0, 0, reliable=False, detail={"reason": "no data"})

    classes = {e for _, e in pairs} | {p for p, _ in pairs}
    per_class: dict[str, dict[str, float]] = {}
    p_sum = r_sum = f_sum = 0.0
    for cls in classes:
        tp = sum(1 for p, e in pairs if p == cls and e == cls)
        fp = sum(1 for p, e in pairs if p == cls and e != cls)
        fn = sum(1 for p, e in pairs if p != cls and e == cls)
        p, r, f = _prf(tp, fp, fn)
        per_class[str(cls)] = {"precision": round(p, 4), "recall": round(r, 4), "f1": round(f, 4), "support": tp + fn}
        p_sum += p
        r_sum += r
        f_sum += f

    k = len(classes)
    macro_p, macro_r, macro_f = p_sum / k, r_sum / k, f_sum / k
    if average == "micro":
        tp = sum(1 for p, e in pairs if p == e)
        macro_p = macro_r = macro_f = tp / n  # micro P=R=F=accuracy for single-label

    return MetricResult(
        "classification_f1",
        macro_f,
        n,
        reliable=n >= min_samples,
        detail={
            "average": average,
            "precision": round(macro_p, 4),
            "recall": round(macro_r, 4),
            "f1": round(macro_f, 4),
            "per_class": per_class,
            **({} if n >= min_samples else {"reason": f"insufficient sample (n={n}<{min_samples})"}),
        },
    )


def extraction_prf(
    pairs: Sequence[tuple[dict[str, Any], dict[str, Any]]],
    *,
    min_samples: int = 10,
) -> MetricResult:
    """Field-level precision/recall/F1 for an extraction cell.

    Each pair is ``(predicted_fields, expected_fields)``. A field counts as a TP
    only when present in both AND equal; an extracted-but-wrong/extra field is a
    FP; an expected-but-missing field is an FN. Aggregated across all examples.
    """
    n = len(pairs)
    if n == 0:
        return MetricResult("extraction_f1", 0.0, 0, reliable=False, detail={"reason": "no data"})

    tp = fp = fn = 0
    for pred, exp in pairs:
        pred = pred or {}
        exp = exp or {}
        for key, exp_val in exp.items():
            if key in pred and pred[key] == exp_val:
                tp += 1
            else:
                fn += 1
        for key, pred_val in pred.items():
            if key not in exp or exp[key] != pred_val:
                fp += 1

    p, r, f = _prf(tp, fp, fn)
    return MetricResult(
        "extraction_f1",
        f,
        n,
        reliable=n >= min_samples,
        detail={
            "precision": round(p, 4), "recall": round(r, 4), "f1": round(f, 4),
            "tp": tp, "fp": fp, "fn": fn,
            **({} if n >= min_samples else {"reason": f"insufficient sample (n={n}<{min_samples})"}),
        },
    )


# --------------------------------------------------------------------------- #
# Brand-voice — Cohen's kappa + on-voice %
# --------------------------------------------------------------------------- #


def cohens_kappa(rater_pairs: Sequence[tuple[Hashable, Hashable]], *, min_samples: int = 10) -> MetricResult:
    """Cohen's κ for two raters' labels over the same items.

    κ = (p_o − p_e) / (1 − p_e); 1.0 = perfect, 0 = chance, <0 = worse than
    chance. Used as the brand-voice *label-quality* gate: κ ≥ 0.6 must hold or the
    on-voice % is not trustworthy (the gate fails on label quality first).
    Perfect agreement with a single label value yields κ = 1.0 (degenerate-but-OK).
    """
    n = len(rater_pairs)
    if n == 0:
        return MetricResult("kappa", 0.0, 0, reliable=False, detail={"reason": "no data"})

    po = sum(1 for a, b in rater_pairs if a == b) / n
    a_counts = Counter(a for a, _ in rater_pairs)
    b_counts = Counter(b for _, b in rater_pairs)
    categories = set(a_counts) | set(b_counts)
    pe = sum((a_counts.get(c, 0) / n) * (b_counts.get(c, 0) / n) for c in categories)

    kappa = 1.0 if pe == 1.0 else (po - pe) / (1 - pe)
    return MetricResult(
        "kappa", kappa, n, reliable=n >= min_samples,
        detail={"p_observed": round(po, 4), "p_expected": round(pe, 4),
                **({} if n >= min_samples else {"reason": f"insufficient sample (n={n}<{min_samples})"})},
    )


def on_voice_rate(consensus_labels: Sequence[bool], *, min_samples: int = 10) -> MetricResult:
    """Fraction of holdout items judged on-voice (the brand-voice ≥90% metric).

    ``consensus_labels`` are the per-example consensus on_voice booleans (majority
    of raters). Pair with :func:`cohens_kappa` ≥ 0.6 — the gate requires both.
    """
    n = len(consensus_labels)
    if n == 0:
        return MetricResult("brand_voice_on_voice_rate", 0.0, 0, reliable=False, detail={"reason": "no data"})
    rate = sum(1 for v in consensus_labels if v) / n
    return MetricResult(
        "brand_voice_on_voice_rate", rate, n, reliable=n >= min_samples,
        detail={"on_voice": sum(consensus_labels), "total": n,
                **({} if n >= min_samples else {"reason": f"insufficient sample (n={n}<{min_samples})"})},
    )
