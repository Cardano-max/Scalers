"""Valid-rate reporting for typed cells.

The acceptance criteria for HARN-02 require reporting "first-pass + after-retry
valid rates". Each :class:`~cells.base.Cell` owns a :class:`ValidRateReport` that
accumulates per-run outcomes and renders those two rates:

* **first-pass valid rate** — fraction of runs whose very first model response
  validated with no repair.
* **after-retry valid rate** — fraction of runs that ended with a valid typed
  object at all (first pass or after one or more repairs).

The gap between them is the share of runs the repair loop rescued; ``1 -
after_retry_rate`` is the share that failed on a code path (raised ``CellError``).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ValidRateReport:
    """Mutable accumulator of cell-run outcomes."""

    label: str = "cell"
    total: int = 0
    first_pass_valid: int = 0
    after_retry_valid: int = 0
    failed: int = 0
    repairs: int = 0
    # Operational failures (network/timeout/connector/etc.) — tracked separately
    # from `failed` so infra noise does not deflate the model-output valid rates.
    errors: int = 0

    def record(self, *, valid: bool, first_pass: bool, repairs: int = 0) -> None:
        """Record one cell run.

        ``valid`` is whether a typed object was ultimately returned; ``first_pass``
        whether it validated with zero repairs; ``repairs`` the number of repair
        retries the run consumed.
        """
        self.total += 1
        self.repairs += repairs
        if valid:
            self.after_retry_valid += 1
            if first_pass:
                self.first_pass_valid += 1
        else:
            self.failed += 1

    def record_error(self) -> None:
        """Record an operational failure (the model never produced a judgeable output).

        Kept out of ``total``/``failed`` so the first-pass / after-retry valid
        rates stay a measure of model-output quality, not infra reliability.
        """
        self.errors += 1

    @property
    def first_pass_rate(self) -> float:
        return self.first_pass_valid / self.total if self.total else 0.0

    @property
    def after_retry_rate(self) -> float:
        return self.after_retry_valid / self.total if self.total else 0.0

    @property
    def repair_rescued(self) -> int:
        """Runs that failed first pass but succeeded after repair."""
        return self.after_retry_valid - self.first_pass_valid

    def render(self) -> str:
        return (
            f"{self.label}: n={self.total} "
            f"first-pass={self.first_pass_rate:.1%} "
            f"after-retry={self.after_retry_rate:.1%} "
            f"(repair rescued {self.repair_rescued}, failed {self.failed}, "
            f"exec errors {self.errors})"
        )
