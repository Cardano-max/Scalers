"""Deep-link + engagement capture on the executed side-effect record (kkg.3).

Two capture paths, both keyed to the idempotency key and idempotent under retry:

* :func:`capture_provider_result` — on side-effect success, records the provider
  result (deep_link / external id / thread_ref). The dispatcher calls this inline
  when it settles; it is also exposed for out-of-band/real-tooling capture.
* :func:`capture_engagement` — engagement (replies / comments / metrics) as it
  arrives via webhook or poll. Merged into the ledger's ``engagement`` jsonb with
  per-entry dedup, so re-delivering the same event (or a retry) never duplicates
  a thread line, a comment, or a metric. Thread/comment text is PII-redacted on
  the way in.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable

import psycopg

from sideeffects.provider import ProviderResult

# Conservative PII redaction (edge case: PII in thread). Emails + long digit runs
# (phone numbers) are masked; a real policy can replace this redactor.
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(?<!\d)(\+?\d[\d\s().-]{7,}\d)(?!\d)")


def redact_pii(text: str) -> str:
    """Mask emails and phone-like digit runs in free text."""
    text = _EMAIL_RE.sub("[redacted-email]", text)
    text = _PHONE_RE.sub("[redacted-phone]", text)
    return text


async def capture_provider_result(
    conn_or_dsn: "psycopg.AsyncConnection | str",
    key: str,
    result: ProviderResult | str,
) -> None:
    """Record the provider result + deep_link on the ledger row for ``key``.

    Idempotent: keyed to the unique idempotency key, a repeat capture overwrites
    with the same values (no dup row, last-write-wins on the provider result)."""
    pr = result if isinstance(result, ProviderResult) else ProviderResult(provider_id=str(result))
    sql = (
        "UPDATE side_effect_ledger"
        " SET provider_result = %s, deep_link = %s, updated_at = now()"
        " WHERE idempotency_key = %s"
    )
    params = (json.dumps(pr.to_jsonb()), pr.deep_link, key)
    if isinstance(conn_or_dsn, str):
        conn = await psycopg.AsyncConnection.connect(conn_or_dsn, autocommit=False)
        try:
            async with conn.transaction():
                await conn.execute(sql, params)
        finally:
            await conn.close()
    else:
        await conn_or_dsn.execute(sql, params)


def _merge_dedup(existing: list[dict], incoming: Iterable[dict], identity) -> list[dict]:
    """Append incoming entries whose identity isn't already present (idempotent)."""
    seen = {identity(e) for e in existing}
    out = list(existing)
    for item in incoming:
        ident = identity(item)
        if ident not in seen:
            seen.add(ident)
            out.append(item)
    return out


async def capture_engagement(
    dsn: str,
    key: str,
    *,
    thread: list[dict[str, Any]] | None = None,
    comments: list[dict[str, Any]] | None = None,
    metrics: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Merge engagement into the ledger row for ``key``, idempotently.

    * ``thread``  — ``[{role: 'in'|'out', name?, text}]`` (replies); deduped by (role, name, text).
    * ``comments``— ``[{name, text, autoReplied}]`` (post comments); deduped by (name, text).
    * ``metrics`` — ``[{label, value}]`` (opened/replied/likes/reach); keyed by label, last value wins.

    Returns the merged engagement object. Thread/comment text is PII-redacted.
    """
    thread = [
        {**e, "text": redact_pii(e.get("text", ""))} for e in (thread or [])
    ]
    comments = [
        {**c, "text": redact_pii(c.get("text", ""))} for c in (comments or [])
    ]
    metrics = list(metrics or [])

    conn = await psycopg.AsyncConnection.connect(dsn, autocommit=False)
    try:
        async with conn.transaction():
            cur = await conn.execute(
                "SELECT engagement FROM side_effect_ledger"
                " WHERE idempotency_key = %s FOR UPDATE",
                (key,),
            )
            row = await cur.fetchone()
            if row is None:
                raise LookupError(f"no executed side effect for key {key!r}")
            current = row[0] or {"thread": [], "comments": [], "metrics": []}

            merged = {
                "thread": _merge_dedup(
                    current.get("thread", []), thread,
                    lambda e: (e.get("role"), e.get("name"), e.get("text")),
                ),
                "comments": _merge_dedup(
                    current.get("comments", []), comments,
                    lambda c: (c.get("name"), c.get("text")),
                ),
                "metrics": _merge_metrics(current.get("metrics", []), metrics),
            }
            await conn.execute(
                "UPDATE side_effect_ledger SET engagement = %s, updated_at = now()"
                " WHERE idempotency_key = %s",
                (json.dumps(merged), key),
            )
            return merged
    finally:
        await conn.close()


def _merge_metrics(existing: list[dict], incoming: list[dict]) -> list[dict]:
    """Metrics are keyed by label; an incoming label updates its value in place."""
    by_label = {m["label"]: dict(m) for m in existing}
    for m in incoming:
        by_label[m["label"]] = dict(m)
    return list(by_label.values())
