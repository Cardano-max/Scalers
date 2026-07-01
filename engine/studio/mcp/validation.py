"""Input validation — the first hardening gate every ``tools/call`` passes.

The MCP spec (2025-11-25, *server/tools* §"Security Considerations") is blunt:
a tool server **MUST** "Validate all tool inputs". Malicious, oversized, or
malformed arguments are rejected here and never reach the wrapped source.

There is no ``jsonschema`` dependency in this engine, so this is a small,
self-contained validator over the JSON-Schema subset the tools actually use
(``type: object`` with typed ``properties``, ``required``, ``enum``,
``maxLength``, ``minLength``, ``minimum``/``maximum``, and
``additionalProperties: false``). It intentionally does the defensive things a
generic validator would not:

  * a hard ceiling on the total serialized argument size (oversized payloads are
    refused, not buffered),
  * per-string length caps,
  * rejection of NUL and other C0 control characters (except ``\\t \\n \\r``) so a
    binary / injection payload cannot ride in through a string field,
  * strict typing — a string where an integer is expected is an error, never a
    silent coercion,
  * unknown top-level keys rejected when ``additionalProperties`` is false.

On any violation it raises :class:`~studio.mcp.errors.InputValidationError` with
an actionable message (the model can read it and retry with fixed arguments —
that is the whole point of the ``isError`` channel). On success it returns a new
dict with declared defaults applied.
"""

from __future__ import annotations

import json
from typing import Any

from studio.mcp.errors import InputValidationError

# Total serialized argument budget. Generous enough for an uploaded CSV / doc,
# but bounded so a caller cannot force the server to buffer an unbounded blob.
MAX_ARGS_BYTES = 1_000_000  # 1 MB
# Default per-string cap when a property does not declare its own ``maxLength``.
DEFAULT_MAX_STRING_LEN = 20_000

# Control characters that are never allowed inside a string argument: everything
# in C0 except tab / newline / carriage-return, plus DEL. NUL is the headline
# case (it truncates C strings and smuggles past naive parsers).
_FORBIDDEN_CTRL = {c for c in range(0x00, 0x20)} - {0x09, 0x0A, 0x0D}
_FORBIDDEN_CTRL.add(0x7F)


def _reject(msg: str) -> "InputValidationError":
    return InputValidationError(msg)


def _first_forbidden_ctrl(s: str) -> str | None:
    for ch in s:
        if ord(ch) in _FORBIDDEN_CTRL:
            return ch
    return None


def _check_string(field: str, value: Any, spec: dict[str, Any]) -> str:
    if not isinstance(value, str):
        raise _reject(f"field {field!r} must be a string, got {type(value).__name__}")
    bad = _first_forbidden_ctrl(value)
    if bad is not None:
        raise _reject(
            f"field {field!r} contains a forbidden control character "
            f"(0x{ord(bad):02x}) — rejected"
        )
    max_len = int(spec.get("maxLength", DEFAULT_MAX_STRING_LEN))
    if len(value) > max_len:
        raise _reject(
            f"field {field!r} is too long: {len(value)} chars > limit {max_len}"
        )
    min_len = spec.get("minLength")
    if min_len is not None and len(value) < int(min_len):
        raise _reject(f"field {field!r} is too short (min {int(min_len)} chars)")
    enum = spec.get("enum")
    if enum is not None and value not in enum:
        raise _reject(f"field {field!r} must be one of {enum}, got {value!r}")
    return value


def _check_integer(field: str, value: Any, spec: dict[str, Any]) -> int:
    # bool is a subclass of int in Python — reject it explicitly so ``true`` is
    # never silently accepted as 1.
    if isinstance(value, bool) or not isinstance(value, int):
        raise _reject(f"field {field!r} must be an integer, got {type(value).__name__}")
    lo, hi = spec.get("minimum"), spec.get("maximum")
    if lo is not None and value < lo:
        raise _reject(f"field {field!r} must be >= {lo}, got {value}")
    if hi is not None and value > hi:
        raise _reject(f"field {field!r} must be <= {hi}, got {value}")
    return value


def _check_boolean(field: str, value: Any) -> bool:
    if not isinstance(value, bool):
        raise _reject(f"field {field!r} must be a boolean, got {type(value).__name__}")
    return value


def _check_value(field: str, value: Any, spec: dict[str, Any]) -> Any:
    typ = spec.get("type")
    if typ == "string":
        return _check_string(field, value, spec)
    if typ == "integer":
        return _check_integer(field, value, spec)
    if typ == "boolean":
        return _check_boolean(field, value)
    # Any other declared type is not used by our tools; be strict rather than
    # permissive — an unexpected schema type is a programming error, not input.
    raise _reject(f"field {field!r} has unsupported schema type {typ!r}")


def validate_arguments(
    input_schema: dict[str, Any], arguments: Any
) -> dict[str, Any]:
    """Validate ``arguments`` against ``input_schema`` and return a cleaned copy.

    Raises :class:`InputValidationError` on any violation. Applies declared
    string/integer/boolean ``default`` values for absent optional fields."""
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise _reject(
            f"arguments must be a JSON object, got {type(arguments).__name__}"
        )

    # Oversized-payload guard, measured on the real serialized size.
    try:
        size = len(json.dumps(arguments).encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise _reject(f"arguments are not JSON-serializable: {exc}") from exc
    if size > MAX_ARGS_BYTES:
        raise _reject(
            f"arguments too large: {size} bytes > limit {MAX_ARGS_BYTES}"
        )

    properties: dict[str, Any] = input_schema.get("properties", {})
    required: list[str] = list(input_schema.get("required", []))
    additional = input_schema.get("additionalProperties", True)

    # Reject unknown keys when the schema forbids them. ``tenant_id`` is always
    # tolerated as a key (the access-control layer inspects it to detect a
    # cross-tenant attempt) but is otherwise ignored by the read.
    if additional is False:
        allowed = set(properties) | {"tenant_id"}
        unknown = [k for k in arguments if k not in allowed]
        if unknown:
            raise _reject(f"unknown argument(s): {sorted(unknown)}")

    for field in required:
        if field not in arguments:
            raise _reject(f"missing required field {field!r}")

    cleaned: dict[str, Any] = {}
    for field, spec in properties.items():
        if field in arguments:
            cleaned[field] = _check_value(field, arguments[field], spec)
        elif "default" in spec:
            cleaned[field] = spec["default"]

    # Preserve a caller-supplied tenant_id so the access-control layer can see it.
    if "tenant_id" in arguments:
        cleaned["tenant_id"] = _check_string(
            "tenant_id", arguments["tenant_id"], {"maxLength": 200}
        )
    return cleaned


__all__ = ["validate_arguments", "MAX_ARGS_BYTES", "DEFAULT_MAX_STRING_LEN"]
