"""The validator bank for typed cells (HARN-02, systemdesign §6.3).

A :class:`Validator` runs deterministic checks a Pydantic schema cannot express
— banned phrase, claim, length, voice similarity — over an already
schema-validated value. Validators are pure code and never call a model
(stack-decision.md: deterministic validators run first and carry most of the
gating).

The interface follows §6.3::

    class Validator(Protocol):
        def check(self, out: BaseModel, ctx: ValidationCtx) -> ValidationResult: ...

A :class:`ValidatorBank` is itself a ``Validator`` (it implements ``check``), so
a bank of banks composes. The cell framework runs the bank inside the model
repair loop: ``ERROR`` issues trigger a repair retry, ``WARN`` issues are
reported but do not block.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence, runtime_checkable


class Severity(str, Enum):
    """How a validation issue affects routing.

    ``ERROR`` blocks the value and triggers a repair retry; ``WARN`` is advisory.
    """

    ERROR = "error"
    WARN = "warn"


@dataclass(frozen=True)
class ValidationIssue:
    """A single problem found by a validator."""

    validator: str
    severity: Severity
    message: str

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return f"[{self.severity.value}] {self.validator}: {self.message}"


@dataclass(frozen=True)
class ValidationCtx:
    """Context passed to a validator.

    Carries the tenant and any per-run knobs a validator might consult (e.g. a
    brand-voice index handle). Kept minimal in Phase 1.
    """

    tenant_id: str | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ValidationResult:
    """Outcome of running a validator (or a bank) over one value."""

    issues: tuple[ValidationIssue, ...] = ()

    @property
    def errors(self) -> tuple[ValidationIssue, ...]:
        return tuple(i for i in self.issues if i.severity is Severity.ERROR)

    @property
    def warnings(self) -> tuple[ValidationIssue, ...]:
        return tuple(i for i in self.issues if i.severity is Severity.WARN)

    @property
    def ok(self) -> bool:
        """True when nothing blocks the value (no ERROR issues)."""
        return not self.errors

    def merge(self, other: "ValidationResult") -> "ValidationResult":
        return ValidationResult(issues=self.issues + other.issues)

    def summary(self) -> str:
        if not self.issues:
            return "ok"
        return "; ".join(str(i) for i in self.issues)


@runtime_checkable
class Validator(Protocol):
    """A deterministic check over a schema-validated value (systemdesign §6.3)."""

    def check(self, out: Any, ctx: ValidationCtx) -> ValidationResult: ...


# A bare function over the value, adapted into the Validator protocol below.
CheckFunc = Callable[[Any], list[ValidationIssue]]


@dataclass(frozen=True)
class FieldValidator:
    """Adapts a ``(value) -> [issues]`` function into the :class:`Validator` protocol."""

    name: str
    fn: CheckFunc

    def check(self, out: Any, ctx: ValidationCtx | None = None) -> ValidationResult:
        return ValidationResult(issues=tuple(self.fn(out)))


@dataclass
class ValidatorBank:
    """An ordered collection of validators, run as one :class:`Validator`."""

    validators: Sequence[Validator] = field(default_factory=tuple)

    def check(self, out: Any, ctx: ValidationCtx | None = None) -> ValidationResult:
        ctx = ctx or ValidationCtx()
        result = ValidationResult()
        for validator in self.validators:
            result = result.merge(validator.check(out, ctx))
        return result


# --------------------------------------------------------------------------- #
# Built-in validators (the bank's default toolkit)
# --------------------------------------------------------------------------- #


def _get(value: Any, field_name: str) -> Any:
    if isinstance(value, dict):
        return value.get(field_name)
    return getattr(value, field_name, None)


def non_empty(field_name: str, *, severity: Severity = Severity.ERROR) -> FieldValidator:
    """Field must be present and, if a string/collection, non-blank."""

    def _fn(value: Any) -> list[ValidationIssue]:
        v = _get(value, field_name)
        empty = v is None or (isinstance(v, (str, list, tuple, dict, set)) and len(v) == 0)
        if isinstance(v, str) and v.strip() == "":
            empty = True
        if empty:
            return [ValidationIssue("non_empty", severity, f"{field_name!r} must not be empty")]
        return []

    return FieldValidator("non_empty", _fn)


def word_count_between(
    field_name: str,
    minimum: int,
    maximum: int,
    *,
    severity: Severity = Severity.ERROR,
) -> FieldValidator:
    """A text field's whitespace-delimited word count must fall in [min, max]."""

    def _fn(value: Any) -> list[ValidationIssue]:
        text = _get(value, field_name)
        if not isinstance(text, str):
            return [ValidationIssue("word_count_between", severity, f"{field_name!r} is not text")]
        n = len(text.split())
        if n < minimum or n > maximum:
            return [
                ValidationIssue(
                    "word_count_between",
                    severity,
                    f"{field_name!r} has {n} words, expected {minimum}-{maximum}",
                )
            ]
        return []

    return FieldValidator("word_count_between", _fn)


# Phrases that read as machine-written boilerplate / AI slop (AF-05 lexicon).
# Curated + tenant-agnostic (per-tenant bans live in the brand-voice DNA). The
# AI-flagger derives its BANNED_SLOP detector from this list (see cells.ai_flagger).
DEFAULT_AI_TELLS: tuple[str, ...] = (
    "as an ai",
    "as a language model",
    "in today's fast-paced world",
    "in conclusion",
    "it is important to note",
    "delve into",
    "delve",
    "tapestry",
    # AF-05 marketing-slop additions (docs/skills/ai-flagger-validator-spec.md).
    "unleash",
    "elevate your",
    "level up",
    "game-changer",
    "game changer",
    "look no further",
    "one-stop",
    "transform your",
    "supercharge",
    "take it to the next level",
    "in the realm of",
    "navigating the",
    "testament to",
    "when it comes to your",
)


def banned_phrases(
    field_name: str,
    phrases: Iterable[str] = DEFAULT_AI_TELLS,
    *,
    severity: Severity = Severity.ERROR,
) -> FieldValidator:
    """Field text must not contain any banned phrase (case-insensitive)."""

    lowered = tuple(p.lower() for p in phrases)

    def _fn(value: Any) -> list[ValidationIssue]:
        text = _get(value, field_name)
        if not isinstance(text, str):
            return []
        hay = text.lower()
        return [
            ValidationIssue("banned_phrases", severity, f"{field_name!r} contains banned phrase {p!r}")
            for p in lowered
            if p in hay
        ]

    return FieldValidator("banned_phrases", _fn)


# Placeholders that mean "the model didn't actually fill this in".
_PLACEHOLDER_RE = re.compile(
    r"\b(todo|tbd|lorem ipsum|insert\s|placeholder|xxx+)\b|\[[^\]]*\]|\{\{[^}]*\}\}",
    re.IGNORECASE,
)


def no_placeholder(field_name: str, *, severity: Severity = Severity.ERROR) -> FieldValidator:
    """Field text must not contain placeholder/template markers."""

    def _fn(value: Any) -> list[ValidationIssue]:
        text = _get(value, field_name)
        if not isinstance(text, str):
            return []
        if _PLACEHOLDER_RE.search(text):
            return [ValidationIssue("no_placeholder", severity, f"{field_name!r} contains placeholder text")]
        return []

    return FieldValidator("no_placeholder", _fn)


def allowed_values(
    field_name: str,
    allowed: Iterable[Any],
    *,
    severity: Severity = Severity.ERROR,
) -> FieldValidator:
    """Field value must be one of an allowed set."""

    allowed_set = set(allowed)

    def _fn(value: Any) -> list[ValidationIssue]:
        v = _get(value, field_name)
        if v not in allowed_set:
            return [
                ValidationIssue(
                    "allowed_values",
                    severity,
                    f"{field_name!r}={v!r} is not one of {sorted(map(str, allowed_set))}",
                )
            ]
        return []

    return FieldValidator("allowed_values", _fn)


def max_items(field_name: str, maximum: int, *, severity: Severity = Severity.WARN) -> FieldValidator:
    """A list field should hold at most ``maximum`` items."""

    def _fn(value: Any) -> list[ValidationIssue]:
        items = _get(value, field_name) or []
        if not isinstance(items, (list, tuple)):
            return []
        if len(items) > maximum:
            return [
                ValidationIssue(
                    "max_items",
                    severity,
                    f"{field_name!r} has {len(items)} items, recommended <= {maximum}",
                )
            ]
        return []

    return FieldValidator("max_items", _fn)
