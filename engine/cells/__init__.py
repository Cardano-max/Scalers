"""Typed LLM cells (HARN-02).

A cell is the only place an LLM runs in Scalers. Each :class:`~cells.base.Cell`
wraps a Pydantic-AI agent that sends a JSON schema with the prompt, validates the
response, repairs missing/mistyped/validator-failing output by retrying, and
returns a typed Pydantic object — or fails on a code path with
:class:`~cells.base.CellError`. Raw model text never escapes a cell.
"""

from cells.base import (
    DEFAULT_MODEL,
    Cell,
    CellError,
    CellInput,
    CellResult,
    TypedCell,
)
from cells.metrics import ValidRateReport
from cells.repair import RepairError, extract_json
from cells.validators import (
    Severity,
    ValidationCtx,
    ValidationIssue,
    ValidationResult,
    Validator,
    ValidatorBank,
)

__all__ = [
    "DEFAULT_MODEL",
    "Cell",
    "CellError",
    "CellInput",
    "CellResult",
    "TypedCell",
    "ValidRateReport",
    "RepairError",
    "extract_json",
    "Severity",
    "ValidationCtx",
    "ValidationIssue",
    "ValidationResult",
    "Validator",
    "ValidatorBank",
]
