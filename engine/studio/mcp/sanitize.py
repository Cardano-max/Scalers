"""Output sanitization — the last hardening gate before a result leaves the server.

The MCP spec (2025-11-25, *server/tools* §"Security Considerations") requires a
tool server to "Sanitize tool outputs". Even though every source wrapped here is
read-only and returns our own normalized domain models, the boundary is enforced
defensively so a surprising value (a stray control character in stored data, an
oversized field, a secret-shaped key, a non-JSON type like ``datetime``) cannot
flow unmodified into the model's context:

  * strings are stripped of NUL / C0 control chars (tab/newline/CR kept) and
    truncated to a per-field cap with an honest ``…[truncated]`` marker,
  * collections are capped in length and nesting depth (defense against a
    pathological blob),
  * ``datetime`` / ``date`` / ``Decimal`` and other non-JSON scalars are coerced
    to strings so the result is always JSON-serializable,
  * keys that look like credentials are redacted (defense in depth — our reads
    do not return secrets, but the gate does not assume that).

Sanitization only ever *removes or truncates*; it never adds or fabricates a
value, so an honest-empty result stays honest-empty.
"""

from __future__ import annotations

from typing import Any

MAX_FIELD_LEN = 20_000          # per-string cap in an output value
MAX_ITEMS = 1_000               # per-list cap
MAX_DEPTH = 12                  # nesting-depth cap
_TRUNCATED = "…[truncated]"

# Control chars stripped from output strings (C0 minus tab/newline/CR, plus DEL).
_STRIP_CTRL = {chr(c) for c in range(0x00, 0x20)} - {"\t", "\n", "\r"}
_STRIP_CTRL.add("\x7f")
_STRIP_TABLE = {ord(c): None for c in _STRIP_CTRL}

# Substrings that mark a key as credential-shaped → its value is redacted.
_SECRET_HINTS = ("password", "secret", "token", "api_key", "apikey", "authorization")
_REDACTED = "[redacted]"


def _looks_secret(key: str) -> bool:
    low = str(key).lower()
    return any(h in low for h in _SECRET_HINTS)


def _clean_str(s: str) -> str:
    s = s.translate(_STRIP_TABLE)
    if len(s) > MAX_FIELD_LEN:
        s = s[: MAX_FIELD_LEN - len(_TRUNCATED)] + _TRUNCATED
    return s


def sanitize_output(value: Any, *, _depth: int = 0) -> Any:
    """Return a JSON-safe, bounded, control-char-free copy of ``value``.

    Pure — never mutates the input and never fabricates a value."""
    if _depth > MAX_DEPTH:
        return _TRUNCATED

    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        return _clean_str(value)

    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in list(value.items())[:MAX_ITEMS]:
            key = _clean_str(str(k))
            if _looks_secret(key):
                out[key] = _REDACTED
            else:
                out[key] = sanitize_output(v, _depth=_depth + 1)
        return out

    if isinstance(value, (list, tuple, set)):
        items = list(value)[:MAX_ITEMS]
        return [sanitize_output(v, _depth=_depth + 1) for v in items]

    # Any other type (datetime, date, Decimal, custom objects) → honest string.
    try:
        iso = value.isoformat()  # type: ignore[attr-defined]
        if isinstance(iso, str):
            return _clean_str(iso)
    except Exception:
        pass
    return _clean_str(str(value))


__all__ = ["sanitize_output", "MAX_FIELD_LEN", "MAX_ITEMS", "MAX_DEPTH"]
