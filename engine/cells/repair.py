"""Deterministic JSON repair for messy model text output.

When a cell is configured for text output (the model emits JSON as prose rather
than a structured tool call), models routinely wrap the payload in markdown
fences, prepend chain-of-thought, or append a sign-off. :func:`extract_json`
salvages the JSON object/array from that noise without calling a model. If it
cannot find well-formed JSON it raises :class:`RepairError` — a code path, never
a silent pass-through of raw text.

This is the textual counterpart to the structured repair loop in
``cells.base``: there, Pydantic-AI repairs missing/mistyped tool arguments;
here, we repair the envelope around free-text JSON before validation.
"""

from __future__ import annotations

import json
import re
from typing import Any

# A fenced block: ```json ... ``` or ``` ... ```
_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.DOTALL)


class RepairError(ValueError):
    """Raised when no well-formed JSON can be recovered from model text."""


def _first_balanced_span(text: str) -> str | None:
    """Return the first balanced ``{...}`` or ``[...]`` span in ``text``.

    String literals are honored so that braces inside strings do not unbalance
    the scan. Returns ``None`` when no balanced span is found.
    """
    open_to_close = {"{": "}", "[": "]"}
    for start, opener in enumerate(text):
        if opener not in open_to_close:
            continue
        closer = open_to_close[opener]
        depth = 0
        in_str = False
        escape = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
        # Unbalanced from this opener; try the next candidate opener.
    return None


def extract_json(text: str) -> Any:
    """Best-effort parse of a JSON value embedded in arbitrary model text.

    Handles, in order: a fenced code block, then the first balanced
    object/array span found anywhere in the text (skipping chain-of-thought
    preamble and trailing sign-offs), then the raw string itself.

    Raises :class:`RepairError` if nothing parses — partial/truncated JSON
    (e.g. an unterminated object) fails here rather than flowing downstream.
    """
    if text is None:
        raise RepairError("no text to repair")

    candidates: list[str] = []

    # 1. Prefer the contents of a fenced block if present.
    fenced = _FENCE_RE.search(text)
    if fenced:
        candidates.append(fenced.group(1).strip())

    # 2. The first balanced object/array anywhere in the (possibly fenced) text.
    for source in ([candidates[0]] if candidates else []) + [text]:
        span = _first_balanced_span(source)
        if span is not None:
            candidates.append(span)

    # 3. The raw text, stripped, as a last resort (covers a bare JSON scalar).
    candidates.append(text.strip())

    for candidate in candidates:
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue

    raise RepairError(
        "could not recover well-formed JSON from model text "
        f"({len(text)} chars; tried {len(candidates)} candidate span(s))"
    )
