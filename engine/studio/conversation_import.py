"""Conversation-CSV import — the Warm Lead Reactivation intake.

The operator exports real customer SMS/DM threads (exact words, one message per
row) and uploads them like any customer CSV. A conversation CSV is detected by
its ``speaker`` + ``text`` columns; each customer's messages become ONE
``lead_conversations`` row (ordered turns, verbatim text) and the customer is
upserted into ``customers`` — so ``conversation_leads`` / the psych analyst /
the dossier read the REAL thread and classify the REAL objection (price,
timing, trust, went-quiet) from what was actually said.

Expected columns (case-insensitive; extra columns ignored):

    conversation_ref, customer_name, customer_email, customer_phone,
    channel, date, time, speaker, sender_label, text

``speaker`` is ``customer`` or ``studio`` (direction aliases accepted by the
conversations store). Text is stored VERBATIM — never rewritten.

HONEST COMPLIANCE CAPTURE: a customer message that is exactly an opt-out word
("stop"/"unsubscribe"/"opt out") marks that customer ``sms_opt_in = FALSE`` at
import time, so no downstream SMS path can treat an opted-out lead as fair
game — the opt-out travels with the data, not just inside the transcript.
"""

from __future__ import annotations

import csv
import io
from typing import Any

CONVERSATION_COLUMNS = frozenset({"speaker", "text"})
_OPT_OUT_WORDS = frozenset({"stop", "unsubscribe", "opt out", "optout"})


def _norm_header(h: str) -> str:
    return (h or "").strip().lstrip("﻿").lower().replace(" ", "_")


def is_conversation_csv(content: str) -> bool:
    """True when the CSV header carries the conversation shape (speaker + text)."""
    try:
        reader = csv.reader(io.StringIO((content or "").lstrip("﻿")))
        headers = {_norm_header(h) for h in (next(reader, []) or [])}
    except Exception:
        return False
    return CONVERSATION_COLUMNS.issubset(headers)


def _rows(content: str) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO((content or "").lstrip("﻿")))
    out = []
    for r in reader:
        out.append({_norm_header(k): (v or "").strip() for k, v in r.items() if k})
    return out


def ingest_conversations_csv(
    tenant_id: str, content: str, *, dsn: str | None = None
) -> dict[str, Any]:
    """Parse + persist a conversation CSV. Returns the honest summary:

        {customers, conversations, turns, customer_ids, opted_out, sample}

    Grouping key: customer_email, else customer_phone, else conversation_ref —
    one lead_conversations row per customer, turns in file order (the export
    order IS the chronology; we never re-sort what the operator gave us)."""
    from studio.conversations import upsert_conversation
    from studio.customer_research import ingest_leads

    rows = _rows(content)
    if not rows:
        raise ValueError("conversation CSV has no data rows")

    groups: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for r in rows:
        key = (
            r.get("customer_email")
            or r.get("customer_phone")
            or r.get("conversation_ref")
            or "unkeyed"
        ).lower()
        g = groups.get(key)
        if g is None:
            g = {
                "name": r.get("customer_name") or "",
                "email": r.get("customer_email") or "",
                "phone": r.get("customer_phone") or "",
                "channel": (r.get("channel") or "sms").lower(),
                "turns": [],
                "opted_out": False,
            }
            groups[key] = g
            order.append(key)
        # Backfill identity fields from later rows (first row may be a studio blast).
        for src, dst in (("customer_name", "name"), ("customer_email", "email"),
                         ("customer_phone", "phone")):
            if not g[dst] and r.get(src):
                g[dst] = r[src]
        text = r.get("text") or ""
        if not text:
            continue
        speaker = (r.get("speaker") or "").lower()
        g["turns"].append({"speaker": speaker, "text": text})
        if speaker == "customer" and text.strip().lower().rstrip(".!") in _OPT_OUT_WORDS:
            g["opted_out"] = True

    # Customers first (ids key the conversation rows). Reuses the customer-CSV
    # upsert so ids/dedupe behave exactly like every other lead import.
    lead_rows = []
    for key in order:
        g = groups[key]
        lead_rows.append({
            "name": g["name"] or key,
            "email": g["email"],
            "phone": g["phone"],
            "notes": "imported from conversation CSV (exact transcript on file)",
        })
    ingest = ingest_leads(tenant_id, lead_rows, dsn=dsn)
    customer_ids = list(ingest.get("customer_ids") or [])

    n_convs = 0
    n_turns = 0
    opted_out: list[str] = []
    for key, cid in zip(order, customer_ids):
        g = groups[key]
        if not g["turns"]:
            continue
        upsert_conversation(
            tenant_id, cid, g["turns"],
            channel=g["channel"], source="conversation-csv", dsn=dsn,
        )
        n_convs += 1
        n_turns += len(g["turns"])
        if g["phone"]:
            _backfill_phone(tenant_id, cid, g["phone"], dsn=dsn)
        if g["opted_out"]:
            opted_out.append(cid)
            _mark_sms_opt_out(tenant_id, cid, dsn=dsn)

    return {
        "customers": len(customer_ids),
        "conversations": n_convs,
        "turns": n_turns,
        "customer_ids": customer_ids,
        "opted_out": opted_out,
        "sample": [
            {"name": groups[k]["name"], "turns": len(groups[k]["turns"]),
             "opted_out": groups[k]["opted_out"]}
            for k in order[:5]
        ],
    }


def _connect(dsn: str | None):
    import os

    import psycopg

    conninfo = dsn or os.environ.get(
        "ENGINE_DATABASE_URL", "postgresql://scalers:scalers@localhost:5432/scalers"
    )
    return psycopg.connect(conninfo, autocommit=True)


def _backfill_phone(
    tenant_id: str, customer_id: str, phone: str, *, dsn: str | None = None
) -> None:
    """Best-effort: keep the transcript's phone on the customer row (empty-only —
    an existing phone is never overwritten by an import)."""
    try:
        with _connect(dsn) as conn:
            conn.execute(
                "UPDATE customers SET phone=%s WHERE tenant_id=%s AND id=%s "
                "AND (phone IS NULL OR phone='')",
                (phone, tenant_id, customer_id),
            )
    except Exception:
        pass


def _mark_sms_opt_out(tenant_id: str, customer_id: str, *, dsn: str | None = None) -> None:
    """The transcript said 'stop' — persist it on the customer row (fail-closed:
    an error here is raised, never swallowed; an opt-out must not be lost)."""
    with _connect(dsn) as conn:
        conn.execute(
            "UPDATE customers SET sms_opt_in = FALSE WHERE tenant_id=%s AND id=%s",
            (tenant_id, customer_id),
        )
