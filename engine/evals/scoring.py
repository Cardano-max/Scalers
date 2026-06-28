"""Pure-code scorers for the per-commit eval gate (rvy.9).

Deterministic, dependency-free implementations of the classification / extraction
/ calibration metrics the Phase-2 gate checks (ADR Decision 4). They run with
``live=False`` over recorded predictions — no model, no keys, reproducible — which
is exactly what the per-commit gate needs. rvy.8 may later swap these for
DeepEval-backed scorers behind the same call sites (ECE/Brier are DeepEval-native);
the gate contract does not change.
"""

from __future__ import annotations

from collections.abc import Sequence


def accuracy(pred: Sequence, exp: Sequence) -> float:
    """Exact-match accuracy. Empty input -> 1.0 (vacuously clean; gate SKIPs empties upstream)."""
    if not exp:
        return 1.0
    hits = sum(1 for p, e in zip(pred, exp, strict=True) if p == e)
    return hits / len(exp)


def recall_of_class(pred: Sequence, exp: Sequence, cls) -> float:
    """Recall for one class: of all true ``cls`` items, the fraction predicted ``cls``.

    Returns 1.0 if the class never appears in ``exp`` (nothing to recall).
    """
    tp = fn = 0
    for p, e in zip(pred, exp, strict=True):
        if e == cls:
            if p == cls:
                tp += 1
            else:
                fn += 1
    denom = tp + fn
    return 1.0 if denom == 0 else tp / denom


def precision_of_class(pred: Sequence, exp: Sequence, cls) -> float:
    """Precision for one class: of all predicted ``cls``, the fraction truly ``cls``.

    Returns 1.0 if the class is never predicted (no false positives possible).
    """
    tp = fp = 0
    for p, e in zip(pred, exp, strict=True):
        if p == cls:
            if e == cls:
                tp += 1
            else:
                fp += 1
    denom = tp + fp
    return 1.0 if denom == 0 else tp / denom


def macro_recall(pred: Sequence, exp: Sequence) -> float:
    """Unweighted mean per-class recall over the classes present in ``exp``."""
    classes = sorted(set(exp), key=str)
    if not classes:
        return 1.0
    return sum(recall_of_class(pred, exp, c) for c in classes) / len(classes)


def macro_f1(pred: Sequence, exp: Sequence) -> float:
    classes = sorted(set(exp), key=str)
    if not classes:
        return 1.0
    total = 0.0
    for c in classes:
        p = precision_of_class(pred, exp, c)
        r = recall_of_class(pred, exp, c)
        total += 0.0 if (p + r) == 0 else 2 * p * r / (p + r)
    return total / len(classes)


def expected_calibration_error(
    confidences: Sequence[float], correct: Sequence[bool], *, bins: int = 10
) -> float:
    """ECE: |confidence - accuracy| averaged over confidence bins, weighted by bin size.

    ``correct[i]`` is whether prediction ``i`` matched the gold label. Empty -> 0.0.
    """
    if not confidences:
        return 0.0
    n = len(confidences)
    edges = [i / bins for i in range(bins + 1)]
    ece = 0.0
    for b in range(bins):
        lo, hi = edges[b], edges[b + 1]
        # last bin is closed on the right so confidence == 1.0 lands somewhere
        idx = [
            i for i, c in enumerate(confidences)
            if (lo <= c < hi) or (b == bins - 1 and c == hi)
        ]
        if not idx:
            continue
        avg_conf = sum(confidences[i] for i in idx) / len(idx)
        acc = sum(1 for i in idx if correct[i]) / len(idx)
        ece += (len(idx) / n) * abs(avg_conf - acc)
    return ece
