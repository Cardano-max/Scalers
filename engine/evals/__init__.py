"""Phase-2 eval gates (EVAL-03 / rvy.8): calibration + accuracy + brand-voice.

The deterministic, hermetic gate layer on top of the eval KB (rvy.2) and ADR
(rvy.1): metric math (:mod:`evals.metrics`), thresholds in config
(:mod:`evals.config`), and pass/fail wiring to the eval_metric store
(:mod:`evals.gates`). The smoke gold set (rvy.10) and the Inspect suite (rvy.7)
feed it; the 439 autonomy gate consumes ``GateReport.promotable()``.
"""

from evals.config import DEFAULT_GATES, GateConfig, GateSpec
from evals.gates import (
    GateFailed,
    GateOutcome,
    GateReport,
    GateStatus,
    accuracy_gates,
    brand_voice_gates,
    calibration_gate,
)
from evals.metrics import (
    MetricResult,
    classification_prf,
    cohens_kappa,
    expected_calibration_error,
    extraction_prf,
    on_voice_rate,
)

__all__ = [
    "DEFAULT_GATES", "GateConfig", "GateSpec",
    "GateFailed", "GateOutcome", "GateReport", "GateStatus",
    "accuracy_gates", "brand_voice_gates", "calibration_gate",
    "MetricResult", "classification_prf", "cohens_kappa",
    "expected_calibration_error", "extraction_prf", "on_voice_rate",
]
