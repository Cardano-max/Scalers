"""Hardening: every cell exception surfaces as a typed CellError (cq4.1).

The dhv.4 wrapper only caught ``UnexpectedModelBehavior`` (schema/parse/validator
exhaustion). Other exceptions — network, timeout, connector, or any unexpected
error at the model boundary — propagated raw and uncaught. These tests assert
that they are now wrapped in :class:`CellExecutionError` (a :class:`CellError`)
on a code path, routed toward bounded recovery, never raw.
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic_ai.exceptions import UnexpectedModelBehavior

from cells.base import CellError, CellExecutionError, CellValidationError
from cells.content_brief import build_content_brief_cell
from tests.conftest import error_model, tool_model


class FlakyConnectorError(RuntimeError):
    """Stand-in for a connector/provider error that is not a ModelBehavior."""


@pytest.mark.parametrize(
    "exc",
    [
        ConnectionError("simulated network drop"),
        TimeoutError("simulated model timeout"),
        FlakyConnectorError("simulated connector 503"),
        ValueError("unexpected provider payload"),
    ],
)
def test_non_modelbehavior_error_becomes_typed_cell_error_sync(exc):
    cell = build_content_brief_cell()
    with pytest.raises(CellExecutionError) as ei:
        cell.run_sync("ctx", model=error_model(exc))
    err = ei.value
    assert isinstance(err, CellError)  # nothing slips past the typed boundary
    assert err.__cause__ is exc        # original error preserved for diagnosis
    assert err.recoverable is True     # routed into bounded recovery
    assert type(exc).__name__ in str(err)


async def test_non_modelbehavior_error_becomes_typed_cell_error_async():
    cell = build_content_brief_cell()
    with pytest.raises(CellExecutionError) as ei:
        await cell.run("ctx", model=error_model(ConnectionError("down")))
    assert isinstance(ei.value, CellError)
    assert isinstance(ei.value.__cause__, ConnectionError)


def test_raw_exception_never_propagates_uncaught():
    # The whole point: callers can catch CellError and be sure nothing leaks.
    cell = build_content_brief_cell()
    leaked = None
    try:
        cell.run_sync("ctx", model=error_model(ConnectionError("boom")))
    except CellError:
        pass  # expected, typed
    except Exception as raw:  # pragma: no cover - this is the bug we fixed
        leaked = raw
    assert leaked is None


def test_execution_error_recorded_separately_from_valid_rate():
    cell = build_content_brief_cell()
    with pytest.raises(CellExecutionError):
        cell.run_sync("ctx", model=error_model(TimeoutError("t")))
    # Operational failure tracked, but it does NOT pollute the valid-rate denominator.
    assert cell.metrics.errors == 1
    assert cell.metrics.total == 0
    assert cell.metrics.failed == 0
    assert "exec errors 1" in cell.metrics.render()


class _ControlFlowSignal(BaseException):
    """A BaseException (not Exception) standing in for KeyboardInterrupt/CancelledError."""


def test_base_exception_is_not_swallowed():
    # BaseException (control-flow: KeyboardInterrupt, CancelledError, SystemExit)
    # must still propagate — we only wrap Exception, never BaseException.
    cell = build_content_brief_cell()
    with pytest.raises(_ControlFlowSignal):
        cell.run_sync("ctx", model=error_model(_ControlFlowSignal()))
    assert cell.metrics.errors == 0  # not recorded as a cell error


def test_validation_exhaustion_still_raises_cell_validation_error():
    # Regression: the original ModelBehavior path is preserved and is a CellError.
    from tests.conftest import VALID_BRIEF
    import copy

    cell = build_content_brief_cell(retries=2)
    short = copy.deepcopy(VALID_BRIEF)
    short["caption"] = "Book now."  # always too short -> repair budget exhausted
    with pytest.raises(CellValidationError) as ei:
        cell.run_sync("ctx", model=tool_model(short))
    assert isinstance(ei.value, CellError)
    assert ei.value.attempts == 3
    # Validation failure DOES count against the valid rate (it's model-output quality).
    assert cell.metrics.failed == 1
    assert cell.metrics.errors == 0


def test_modelbehavior_and_execution_paths_are_distinct_subclasses():
    assert issubclass(CellValidationError, CellError)
    assert issubclass(CellExecutionError, CellError)
    assert not issubclass(CellExecutionError, CellValidationError)
    # Sanity: UnexpectedModelBehavior is not itself a CellError (we wrap it).
    assert not issubclass(UnexpectedModelBehavior, CellError)
