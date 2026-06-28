"""Eval-spine datasets + helpers (Phase 2).

Currently holds the SMOKE gold set (rvy.10): a tiny, synthetic, deterministically
labeled dataset on the TEST tenant that exercises the eval pipeline end-to-end
(loaders -> scorers -> eval_metric -> CI gate) BEFORE the real human-labeled gold
sets exist. It explicitly does NOT satisfy any real quality/autonomy gate.
"""

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
    "SMOKE_TENANT",
    "SMOKE_SPLIT",
    "LABEL_VERSION",
    "ORACLE_RATER",
    "SmokeExample",
    "load_smoke_gold_set",
    "get_smoke_set",
    "iter_smoke_examples",
    "metric_flip_examples",
]
