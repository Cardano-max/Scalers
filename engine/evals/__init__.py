"""Phase-2 eval spine: datasets, metrics, and gates.

The deterministic, hermetic eval layer on top of the eval KB (rvy.2) and ADR
(rvy.1). Two parts:

* **Gates (EVAL-03 / rvy.8)** — metric math (:mod:`evals.metrics`), thresholds in
  config (:mod:`evals.config`), and pass/fail wiring to the eval_metric store
  (:mod:`evals.gates`). The 439 autonomy gate consumes ``GateReport.promotable()``.
* **SMOKE gold set (rvy.10)** — a tiny, synthetic, deterministically labeled
  dataset on the TEST tenant that exercises the eval pipeline end-to-end
  (loaders -> scorers -> eval_metric -> CI gate) BEFORE the real human-labeled
  gold sets exist. It explicitly does NOT satisfy any real quality/autonomy gate.

The smoke set feeds the gates (and the Inspect suite, rvy.7) so the gate wiring
is exercised now, hermetically, in-repo.
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
from evals.smoke_gold_set import (
    LABEL_VERSION,
    ORACLE_RATER,
    SMOKE_SPLIT,
    SMOKE_TENANT,
    SmokeExample,
    get_smoke_set,
    iter_smoke_examples,
    load_smoke_gold_set,
    metric_flip_examples,
)

__all__ = [
    # Gates (rvy.8)
    "DEFAULT_GATES", "GateConfig", "GateSpec",
    "GateFailed", "GateOutcome", "GateReport", "GateStatus",
    "accuracy_gates", "brand_voice_gates", "calibration_gate",
    "MetricResult", "classification_prf", "cohens_kappa",
    "expected_calibration_error", "extraction_prf", "on_voice_rate",
    # SMOKE gold set (rvy.10)
    "SMOKE_TENANT", "SMOKE_SPLIT", "LABEL_VERSION", "ORACLE_RATER",
    "SmokeExample", "load_smoke_gold_set", "get_smoke_set",
    "iter_smoke_examples", "metric_flip_examples",
]
