"""The typed-cell framework (HARN-02, systemdesign §6.3).

Every LLM call in Scalers is a :class:`Cell`. A cell ALWAYS returns a validated
Pydantic model or raises :class:`CellError`; raw model text never leaves this
boundary. Internally it wraps a Pydantic-AI agent that sends the schema with the
prompt, validates the response on arrival, and auto-retries (repairs) on a
missing/mistyped field or a deterministic-validator failure. After the retry
budget is spent it fails on a code path.

Two repair surfaces:

* **Structured mode** (default) — the model fills the schema via a tool call.
  Pydantic-AI repairs missing/mistyped fields; an ``output_validator`` runs the
  :class:`~cells.validators.ValidatorBank` and raises ``ModelRetry`` on any
  ``ERROR`` issue.
* **Text mode** (``text_output=True``) — the model emits JSON as prose. A
  ``TextOutput`` parser runs :func:`~cells.repair.extract_json` (markdown fences /
  chain-of-thought / trailing sign-offs), then schema validation, then the
  validator bank, raising ``ModelRetry`` at any stage.

Each cell runs at ``temperature=0`` against a PINNED model id by default
(HARN-06) and owns a :class:`~cells.metrics.ValidRateReport` (``cell.metrics``)
so first-pass and after-retry valid rates can be reported.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, Sequence, TypeVar

from pydantic import BaseModel, ValidationError
from pydantic_ai import Agent, ModelRetry, TextOutput
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.messages import ModelMessage, RetryPromptPart
from pydantic_ai.models import KnownModelName, Model

from cells.metrics import ValidRateReport
from cells.repair import RepairError, extract_json
from cells.validators import ValidationCtx, ValidationResult, ValidatorBank
from metrics import time_cell  # Prometheus cell-latency histogram (13u)

TOut = TypeVar("TOut", bound=BaseModel)

# The prompt handed to a cell. Phase 1 cells take a plain string.
CellInput = str

# Pinned default under the 8sk MODEL POLICY (operator order 2026-07-02):
# haiku-4.5 for everything, sonnet-4.5 the absolute ceiling (harness.config).
# Cells run at temp-0. Production runs need ANTHROPIC_API_KEY; tests override
# the model with Test/Function models.
DEFAULT_MODEL: KnownModelName = "anthropic:claude-haiku-4-5"


class CellError(Exception):
    """A cell failed on a code path. Raw text never escapes; the harness's
    bounded-recovery layer (HARN-03) catches this instead of an uncaught error.

    Every exception out of a cell run is one of the two subclasses below, so a
    caller can ``except CellError`` and know nothing slipped past untyped.
    ``recoverable`` is a hint for the bounded-recovery router (retry → regenerate
    → human-review).
    """

    def __init__(
        self,
        message: str,
        *,
        attempts: int = 1,
        cause: Exception | None = None,
        recoverable: bool = True,
    ) -> None:
        super().__init__(message)
        self.attempts = attempts
        self.recoverable = recoverable
        self.__cause__ = cause


class CellValidationError(CellError):
    """The model's output could not be repaired into a schema- and validator-valid
    object within the retry budget (the ``UnexpectedModelBehavior`` case)."""


class CellExecutionError(CellError):
    """The cell failed to execute for a non-output reason — network, timeout,
    connector, cancellation-adjacent, or any other exception raised while running
    the model. Surfaced as a typed error so it never propagates raw."""


@dataclass(frozen=True)
class CellResult(Generic[TOut]):
    """A validated cell output plus the metrics needed to report valid rates."""

    value: TOut
    first_pass_valid: bool
    attempts: int
    repairs: int
    validation: ValidationResult


def _count_repairs(messages: Sequence[ModelMessage]) -> int:
    """Count repair retries by tallying RetryPromptParts in the message history.

    Pydantic-AI appends a ``RetryPromptPart`` each time it asks the model to fix
    schema-invalid output or a ``ModelRetry`` raised by a validator — so this
    counts both kinds of repair uniformly.
    """
    return sum(
        1
        for m in messages
        for p in getattr(m, "parts", [])
        if isinstance(p, RetryPromptPart)
    )


class Cell(Generic[TOut]):
    """A bounded LLM cell that returns a schema-validated typed object (§6.3)."""

    def __init__(
        self,
        name: str,
        schema: type[TOut],
        *,
        instructions: str,
        validators: ValidatorBank | None = None,
        model: Model | KnownModelName = DEFAULT_MODEL,
        retries: int = 2,
        temperature: float = 0.0,
        text_output: bool = False,
    ) -> None:
        self.name = name
        self.schema = schema
        self.model = model
        self.temperature = temperature
        self.retries = retries
        self.text_output = text_output
        self.validators = validators or ValidatorBank()
        self.metrics = ValidRateReport(label=name)

        def run_bank(value: TOut) -> TOut:
            result = self.validators.check(value, ValidationCtx())
            if not result.ok:
                # Repairable: ask the model to try again with the concrete reasons.
                raise ModelRetry(
                    "output failed validation: "
                    + "; ".join(str(i) for i in result.errors)
                )
            return value

        if text_output:

            def _parse(text: str) -> TOut:
                try:
                    data = extract_json(text)
                except RepairError as exc:
                    raise ModelRetry(f"output was not parseable JSON: {exc}")
                try:
                    value = self.schema.model_validate(data)
                except ValidationError as exc:
                    raise ModelRetry(f"output did not match schema: {exc}")
                return run_bank(value)

            self._agent: Agent = Agent(
                model,
                output_type=TextOutput(_parse),
                instructions=instructions,
                model_settings={"temperature": temperature},
                retries=retries,
                defer_model_check=True,
            )
        else:
            self._agent = Agent(
                model,
                output_type=schema,
                instructions=instructions,
                model_settings={"temperature": temperature},
                retries=retries,
                defer_model_check=True,
            )

            @self._agent.output_validator
            def _validate(value: TOut) -> TOut:  # pragma: no cover - exercised via run()
                return run_bank(value)

    # -- internals ---------------------------------------------------------- #

    def _result_from(self, output: TOut, messages: Sequence[ModelMessage]) -> CellResult[TOut]:
        repairs = _count_repairs(messages)
        # Final report over the value that actually passed (always ok by here).
        report = self.validators.check(output, ValidationCtx())
        self.metrics.record(valid=True, first_pass=repairs == 0, repairs=repairs)
        return CellResult(
            value=output,
            first_pass_valid=repairs == 0,
            attempts=repairs + 1,
            repairs=repairs,
            validation=report,
        )

    def _on_validation_failure(self, exc: UnexpectedModelBehavior) -> CellValidationError:
        """Repair budget exhausted: typed failure, recorded against the valid rate."""
        self.metrics.record(valid=False, first_pass=False, repairs=self.retries)
        return CellValidationError(
            f"cell {self.name!r} could not produce valid output after "
            f"{self.retries} repair attempt(s)",
            attempts=self.retries + 1,
            cause=exc,
        )

    def _on_execution_failure(self, exc: Exception) -> CellExecutionError:
        """Any non-output exception (network/timeout/connector/...): typed, recorded
        as an operational error, routed into bounded recovery — never uncaught."""
        self.metrics.record_error()
        return CellExecutionError(
            f"cell {self.name!r} failed to execute: {type(exc).__name__}: {exc}",
            attempts=1,
            cause=exc,
        )

    # -- public API (systemdesign §6.3: run -> TOut or raise) --------------- #

    async def run(
        self,
        prompt: CellInput,
        *,
        model: Model | KnownModelName | None = None,
        message_history: Sequence[ModelMessage] | None = None,
    ) -> TOut:
        """Run the cell and return the validated typed object, or raise ``CellError``."""
        return (await self.run_detailed(prompt, model=model, message_history=message_history)).value

    def run_sync(
        self,
        prompt: CellInput,
        *,
        model: Model | KnownModelName | None = None,
        message_history: Sequence[ModelMessage] | None = None,
    ) -> TOut:
        """Synchronous :meth:`run`."""
        return self.run_detailed_sync(prompt, model=model, message_history=message_history).value

    async def run_detailed(
        self,
        prompt: CellInput,
        *,
        model: Model | KnownModelName | None = None,
        message_history: Sequence[ModelMessage] | None = None,
    ) -> CellResult[TOut]:
        """Like :meth:`run` but returns the full :class:`CellResult` (value + metrics)."""
        with time_cell(cell=self.name):
            try:
                result = await self._agent.run(prompt, model=model, message_history=message_history)
            except CellError:
                raise  # already typed (e.g. raised from a validator) — don't double-wrap
            except UnexpectedModelBehavior as exc:
                raise self._on_validation_failure(exc) from exc
            except Exception as exc:
                # network/timeout/connector/anything else — never let it propagate raw.
                raise self._on_execution_failure(exc) from exc
            return self._result_from(result.output, result.all_messages())

    def run_detailed_sync(
        self,
        prompt: CellInput,
        *,
        model: Model | KnownModelName | None = None,
        message_history: Sequence[ModelMessage] | None = None,
    ) -> CellResult[TOut]:
        """Synchronous :meth:`run_detailed`."""
        with time_cell(cell=self.name):
            try:
                result = self._agent.run_sync(prompt, model=model, message_history=message_history)
            except CellError:
                raise  # already typed (e.g. raised from a validator) — don't double-wrap
            except UnexpectedModelBehavior as exc:
                raise self._on_validation_failure(exc) from exc
            except Exception as exc:
                # network/timeout/connector/anything else — never let it propagate raw.
                raise self._on_execution_failure(exc) from exc
            return self._result_from(result.output, result.all_messages())


# Back-compat / descriptive alias.
TypedCell = Cell
