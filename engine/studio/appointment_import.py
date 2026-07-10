"""Appointment-history CSV import — booking exports become real per-customer history.

The operator exports appointment history from their booking system (one row per
SESSION DAY — a multi-session appointment repeats its ``appointment_id`` across
several slot dates) and uploads it like any customer CSV. An appointment CSV is
detected by its ``appointment_id`` + customer identity + date columns; rows are
grouped per customer (email, else phone, else name), the customer is upserted
through the same lead-ingest path as every other CSV, every session day lands in
the ``appointments`` table (see ``infra/initdb/25-appointments.sql``), and ONE
per-customer summary memory is written into ``memories`` (subject_type
``customer``) — the store ``lookup_lead`` already reads on every dossier build,
so the history is USED by drafting, not parked.

Expected columns (case-insensitive; extra columns ignored):

    appointment_id, status, tattoo_description, style, size, placement, deposit,
    total, internalNote, slot_id, slot_date, slot_time, duration, slot_type,
    quotedAmount, slot_title, tbd, customer_name, customer_email, customer_phone

(a variant export carries ``date``/``time``/``type``/``title`` instead of the
``slot_*`` names; both are accepted).

Why NOT ``tattoo_history``: its live shape (21-customer-personas.sql) is
``style/artist/date/notes`` only — no ``appointment_id``, no amounts, and no
unique key, so a re-upload there could only duplicate. ``appointments`` is
additive and keyed on (tenant, appointment_id, slot_date), so re-uploading the
same export is a no-op.

HONESTY: every stored value is the CSV's own value — blank stays NULL, amounts
that don't parse as plain numbers ("TBD") stay NULL, internal notes are stored
verbatim, and nothing (style, amount, date) is ever guessed or invented.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any

# Header shape that marks a booking export: the booking system's own id, a
# customer identity column, and a session-day date column.
_IDENTITY_COLUMNS = frozenset({"customer_email", "customer_name"})
_DATE_COLUMNS = frozenset({"slot_date", "date"})

_AMOUNT_RE = re.compile(r"^-?\d+(\.\d+)?$")


def _norm_header(h: str) -> str:
    return (h or "").strip().lstrip("﻿").lower().replace(" ", "_")


def is_appointment_csv(content: str) -> bool:
    """True when the CSV header carries the booking-export shape:
    ``appointment_id`` + (customer_email or customer_name) + (slot_date or date)."""
    try:
        reader = csv.reader(io.StringIO((content or "").lstrip("﻿")))
        headers = {_norm_header(h) for h in (next(reader, []) or [])}
    except Exception:
        return False
    return (
        "appointment_id" in headers
        and bool(headers & _IDENTITY_COLUMNS)
        and bool(headers & _DATE_COLUMNS)
    )


def _rows(content: str) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO((content or "").lstrip("﻿")))
    out = []
    for r in reader:
        out.append({_norm_header(k): (v or "").strip() for k, v in r.items() if k})
    return out


def _parse_amount(raw: str | None) -> Decimal | None:
    """A clean numeric (optionally $/£/€-prefixed, comma-grouped) parses; anything
    else — "TBD", "", prose — stays None. An amount is never guessed."""
    s = (raw or "").strip().lstrip("$£€").replace(",", "").strip()
    if not s or not _AMOUNT_RE.match(s):
        return None
    return Decimal(s)


def _parse_date(raw: str | None) -> date | None:
    """Parse a session-day date when the format is recognizable (ISO first, then
    the common US booking-export forms). Unparseable stays None — the verbatim
    string is kept in ``slot_date`` either way, so nothing is lost or invented."""
    s = (raw or "").strip()
    if not s:
        return None
    for candidate in (s, s[:10]):
        try:
            return date.fromisoformat(candidate)
        except ValueError:
            pass
    for fmt in ("%Y/%m/%d", "%m/%d/%Y", "%m/%d/%y", "%d %b %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def _session_from_row(r: dict[str, str], appt_id: str, slot_date: str) -> dict[str, Any]:
    """One session-day dict, straight off the row: blanks -> None, amounts parsed
    or None, the internal note verbatim. Accepts both the ``slot_*`` and the
    bare (``date``/``time``/``type``/``title``) header variants."""
    return {
        "appointment_id": appt_id,
        "slot_id": r.get("slot_id") or None,
        "slot_date": slot_date,
        "slot_date_parsed": _parse_date(slot_date),
        "slot_time": (r.get("slot_time") or r.get("time")) or None,
        "duration": r.get("duration") or None,
        "slot_type": (r.get("slot_type") or r.get("type")) or None,
        "slot_title": (r.get("slot_title") or r.get("title")) or None,
        "status": r.get("status") or None,
        "tattoo_description": r.get("tattoo_description") or None,
        "style": r.get("style") or None,
        "size": r.get("size") or None,
        "placement": r.get("placement") or None,
        "deposit": _parse_amount(r.get("deposit")),
        "total": _parse_amount(r.get("total")),
        "quoted_amount": _parse_amount(r.get("quotedamount") or r.get("quoted_amount")),
        "tbd": r.get("tbd") or None,
        "internal_note": (r.get("internalnote") or r.get("internal_note")) or None,
    }


def ingest_appointments_csv(
    tenant_id: str,
    content: str,
    *,
    dsn: str | None = None,
    source_file: str | None = None,
) -> dict[str, Any]:
    """Parse + persist an appointment-history CSV. Returns the honest summary:

        {customers, appointments, sessions, sessions_inserted, sessions_existing,
         skipped_rows, customer_ids, memories, performance, sample}

    Grouping key: customer_email, else customer_phone, else customer_name — one
    customer per group, one ``appointments`` row per (appointment_id, slot date).
    Idempotent end to end: session rows key on (tenant, appointment_id, slot_date),
    the customer upsert keys on tenant+email (phone/name-matched when email-less),
    and the summary memory upserts on its content hash — a re-upload changes
    nothing and reports ``sessions_inserted: 0``."""
    from studio.customer_research import ingest_leads

    rows = _rows(content)
    if not rows:
        raise ValueError("appointment CSV has no data rows")

    groups: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    seen_keys: set[tuple[str, str]] = set()
    skipped = 0
    for r in rows:
        appt_id = r.get("appointment_id") or ""
        if not appt_id:
            skipped += 1  # no booking id -> no stable natural key; a key is never invented
            continue
        slot_date = r.get("slot_date") or r.get("date") or ""
        nk = (appt_id, slot_date)
        if nk in seen_keys:
            continue  # exact in-file repeat of the same session day
        seen_keys.add(nk)
        key = (
            r.get("customer_email")
            or r.get("customer_phone")
            or r.get("customer_name")
            or "unkeyed"
        ).lower()
        g = groups.get(key)
        if g is None:
            g = {
                "name": r.get("customer_name") or "",
                "email": r.get("customer_email") or "",
                "phone": r.get("customer_phone") or "",
                "sessions": [],
            }
            groups[key] = g
            order.append(key)
        # Backfill identity fields from later rows of the same customer.
        for src, dst in (("customer_name", "name"), ("customer_email", "email"),
                         ("customer_phone", "phone")):
            if not g[dst] and r.get(src):
                g[dst] = r[src]
        g["sessions"].append(_session_from_row(r, appt_id, slot_date))

    # Customers first (ids key the session rows). Email-keyed groups go through the
    # canonical lead upsert; email-less groups first try a phone/name match so a
    # re-upload never mints a duplicate row (upsert_lead only keys on email).
    resolved: dict[str, str] = {}
    to_ingest: list[str] = []
    for key in order:
        g = groups[key]
        if not g["email"]:
            found = _find_customer(tenant_id, phone=g["phone"], name=g["name"], dsn=dsn)
            if found:
                resolved[key] = found
                continue
        to_ingest.append(key)
    if to_ingest:
        lead_rows = [
            {
                "name": groups[k]["name"] or k,
                "email": groups[k]["email"],
                "phone": groups[k]["phone"],
                "notes": "imported from appointment history CSV",
            }
            for k in to_ingest
        ]
        ingest = ingest_leads(tenant_id, lead_rows, dsn=dsn)
        for k, cid in zip(to_ingest, list(ingest.get("customer_ids") or [])):
            resolved[k] = cid
    customer_ids = [resolved[k] for k in order]

    # Session rows: flatten with the resolved customer + verbatim identity fields.
    all_sessions: list[dict[str, Any]] = []
    for key, cid in zip(order, customer_ids):
        g = groups[key]
        for s in g["sessions"]:
            all_sessions.append({
                **s,
                "customer_id": cid,
                "customer_name": g["name"] or None,
                "customer_email": g["email"] or None,
                "customer_phone": g["phone"] or None,
                "source_file": source_file,
            })
    inserted, existing = _persist_sessions(tenant_id, all_sessions, dsn=dsn)

    # ONE summary memory per customer — subject_type='customer' is what
    # ``lookup_lead`` lists on every dossier build, so the history is read, not
    # parked. MemoryStore upserts on content hash, so a re-upload rewrites the
    # same row. A memory failure is reported, never hidden — and never blocks
    # the already-persisted session rows.
    n_memories = 0
    memory_errors: list[str] = []
    for key, cid in zip(order, customer_ids):
        g = groups[key]
        try:
            _write_customer_memory(
                tenant_id, cid, _summary_text(g), _summary_metadata(g), dsn=dsn
            )
            n_memories += 1
        except Exception as exc:
            memory_errors.append(f"{cid}: {type(exc).__name__}: {exc}")
        if g["phone"]:
            _backfill_phone(tenant_id, cid, g["phone"], dsn=dsn)

    out = {
        "customers": len(customer_ids),
        "appointments": len({s["appointment_id"] for s in all_sessions}),
        "sessions": len(all_sessions),
        "sessions_inserted": inserted,
        "sessions_existing": existing,
        "skipped_rows": skipped,
        "customer_ids": customer_ids,
        "memories": n_memories,
        "performance": _performance(all_sessions, len(customer_ids)),
        "sample": [
            {
                "name": groups[k]["name"],
                "appointments": len({s["appointment_id"] for s in groups[k]["sessions"]}),
                "sessions": len(groups[k]["sessions"]),
            }
            for k in order[:5]
        ],
    }
    if memory_errors:
        out["memory_errors"] = memory_errors[:10]
    return out


# --------------------------------------------------------------------------- #
# Aggregates + the per-customer summary — computed ONLY from what the CSV said.
# --------------------------------------------------------------------------- #


def _deposits_per_appointment(sessions: list[dict[str, Any]]) -> list[Decimal]:
    """One deposit per appointment_id (the export repeats the appointment's deposit
    on every session-day row — summing per row would double-count)."""
    per_appt: dict[str, Decimal] = {}
    for s in sessions:
        if s["deposit"] is not None and s["appointment_id"] not in per_appt:
            per_appt[s["appointment_id"]] = s["deposit"]
    return list(per_appt.values())


def _performance(sessions: list[dict[str, Any]], n_customers: int) -> dict[str, Any]:
    """The artist-performance aggregates for this file: sessions, unique customers,
    total deposits (per appointment, parsed values only), and the date span of the
    parseable session dates. Honest: unparseable dates are counted, not guessed."""
    deposits = _deposits_per_appointment(sessions)
    dates = sorted(s["slot_date_parsed"] for s in sessions if s["slot_date_parsed"])
    return {
        "sessions": len(sessions),
        "appointments": len({s["appointment_id"] for s in sessions}),
        "unique_customers": n_customers,
        "total_deposits": float(sum(deposits)) if deposits else None,
        "date_span": (
            {"from": dates[0].isoformat(), "to": dates[-1].isoformat()} if dates else None
        ),
        "unparsed_dates": sum(1 for s in sessions if s["slot_date_parsed"] is None),
    }


def _uniq(values: list[Any]) -> list[Any]:
    seen: set[Any] = set()
    out = []
    for v in values:
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def _summary_text(g: dict[str, Any]) -> str:
    """The ONE per-customer history memory — every clause traces to a CSV value;
    a field the export left blank simply does not appear."""
    sessions: list[dict[str, Any]] = g["sessions"]
    n_appts = len({s["appointment_id"] for s in sessions})
    head = (
        f"Appointment history (imported from booking export): {n_appts} appointment(s), "
        f"{len(sessions)} session day(s)"
    )
    dates = sorted(s["slot_date_parsed"] for s in sessions if s["slot_date_parsed"])
    if dates:
        head += f" between {dates[0].isoformat()} and {dates[-1].isoformat()}"
    parts = [head]
    styles = _uniq([s["style"] for s in sessions])
    if styles:
        parts.append("styles: " + ", ".join(styles))
    statuses = _uniq([s["status"] for s in sessions])
    if statuses:
        parts.append("statuses: " + ", ".join(statuses))
    deposits = _deposits_per_appointment(sessions)
    if deposits:
        parts.append(f"deposits recorded: {sum(deposits):.2f}")
    latest = _latest_described(sessions)
    if latest is not None:
        desc = latest["tattoo_description"]
        if len(desc) > 200:
            desc = desc[:200] + "…"
        where = f" ({latest['placement']})" if latest["placement"] else ""
        parts.append(f"latest piece on file: {desc}{where}")
    return "; ".join(parts) + "."


def _latest_described(sessions: list[dict[str, Any]]) -> dict[str, Any] | None:
    described = [s for s in sessions if s["tattoo_description"]]
    if not described:
        return None
    dated = [s for s in described if s["slot_date_parsed"]]
    if dated:
        return max(dated, key=lambda s: s["slot_date_parsed"])
    return described[-1]


def _summary_metadata(g: dict[str, Any]) -> dict[str, Any]:
    sessions: list[dict[str, Any]] = g["sessions"]
    dates = sorted(s["slot_date_parsed"] for s in sessions if s["slot_date_parsed"])
    deposits = _deposits_per_appointment(sessions)
    meta: dict[str, Any] = {
        "source": "appointment-csv",
        "appointments": len({s["appointment_id"] for s in sessions}),
        "sessions": len(sessions),
    }
    if dates:
        meta["first_date"] = dates[0].isoformat()
        meta["last_date"] = dates[-1].isoformat()
    styles = _uniq([s["style"] for s in sessions])
    if styles:
        meta["styles"] = styles
    if deposits:
        meta["total_deposits"] = float(sum(deposits))
    return meta


# --------------------------------------------------------------------------- #
# Persistence — the additive ``appointments`` table + the seams the tests fake.
# --------------------------------------------------------------------------- #


def _connect(dsn: str | None):
    import os

    import psycopg

    conninfo = dsn or os.environ.get(
        "ENGINE_DATABASE_URL", "postgresql://scalers:scalers@localhost:5432/scalers"
    )
    return psycopg.connect(conninfo, autocommit=True)


def ensure_schema(dsn: str | None = None) -> None:
    """Idempotently create ``appointments`` — the runtime twin of
    ``infra/initdb/25-appointments.sql`` (additive-only; a no-op once provisioned).

    One row per SESSION DAY, keyed on (tenant_id, appointment_id, slot_date) —
    ``slot_date`` is the export's VERBATIM date string so the key works even for
    a format we can't parse; ``slot_date_parsed`` carries the typed date when the
    format was recognizable."""
    with _connect(dsn) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS appointments (
                id                 BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                tenant_id          TEXT NOT NULL,
                customer_id        TEXT,
                appointment_id     TEXT NOT NULL,
                slot_id            TEXT,
                slot_date          TEXT NOT NULL,
                slot_date_parsed   DATE,
                slot_time          TEXT,
                duration           TEXT,
                slot_type          TEXT,
                slot_title         TEXT,
                status             TEXT,
                tattoo_description TEXT,
                style              TEXT,
                size               TEXT,
                placement          TEXT,
                deposit            NUMERIC,
                total              NUMERIC,
                quoted_amount      NUMERIC,
                tbd                TEXT,
                internal_note      TEXT,
                customer_name      TEXT,
                customer_email     TEXT,
                customer_phone     TEXT,
                source_file        TEXT,
                created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            CREATE UNIQUE INDEX IF NOT EXISTS appointments_natural_key
                ON appointments (tenant_id, appointment_id, slot_date);
            CREATE INDEX IF NOT EXISTS appointments_customer_idx
                ON appointments (tenant_id, customer_id);
            """
        )


def _persist_sessions(
    tenant_id: str, sessions: list[dict[str, Any]], *, dsn: str | None = None
) -> tuple[int, int]:
    """INSERT session rows; an existing (tenant, appointment_id, slot_date) key is
    left untouched (DO NOTHING) so a re-upload never duplicates or clobbers.
    Returns ``(inserted, existing)``."""
    ensure_schema(dsn)
    inserted = 0
    with _connect(dsn) as conn:
        for s in sessions:
            cur = conn.execute(
                """
                INSERT INTO appointments
                    (tenant_id, customer_id, appointment_id, slot_id, slot_date,
                     slot_date_parsed, slot_time, duration, slot_type, slot_title,
                     status, tattoo_description, style, size, placement,
                     deposit, total, quoted_amount, tbd, internal_note,
                     customer_name, customer_email, customer_phone, source_file)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (tenant_id, appointment_id, slot_date) DO NOTHING
                """,
                (
                    tenant_id, s["customer_id"], s["appointment_id"], s["slot_id"],
                    s["slot_date"], s["slot_date_parsed"], s["slot_time"], s["duration"],
                    s["slot_type"], s["slot_title"], s["status"], s["tattoo_description"],
                    s["style"], s["size"], s["placement"], s["deposit"], s["total"],
                    s["quoted_amount"], s["tbd"], s["internal_note"], s["customer_name"],
                    s["customer_email"], s["customer_phone"], s["source_file"],
                ),
            )
            inserted += cur.rowcount
    return inserted, len(sessions) - inserted


def _write_customer_memory(
    tenant_id: str,
    customer_id: str,
    text: str,
    metadata: dict[str, Any],
    *,
    dsn: str | None = None,
) -> str:
    """One dossier-visible memory row (idempotent on the store's content hash)."""
    from memory.store import MemoryStore

    store = MemoryStore(dsn=dsn)
    store.ensure_schema()
    return store.write(
        tenant_id=tenant_id,
        subject_type="customer",
        subject_id=customer_id,
        text=text,
        metadata=metadata,
    )


def _find_customer(
    tenant_id: str, *, phone: str, name: str, dsn: str | None = None
) -> str | None:
    """Email-less groups: match an existing customer by exact phone, else exact
    (case-insensitive) name — ``upsert_lead`` only keys on email, so without this
    every re-upload would mint a duplicate row for a phone-only customer."""
    try:
        with _connect(dsn) as conn:
            if phone:
                row = conn.execute(
                    "SELECT id FROM customers WHERE tenant_id=%s AND phone=%s LIMIT 1",
                    (tenant_id, phone),
                ).fetchone()
                if row:
                    return row[0]
            if name:
                row = conn.execute(
                    "SELECT id FROM customers WHERE tenant_id=%s "
                    "AND lower(name)=lower(%s) LIMIT 1",
                    (tenant_id, name),
                ).fetchone()
                if row:
                    return row[0]
    except Exception:
        return None
    return None


def _backfill_phone(
    tenant_id: str, customer_id: str, phone: str, *, dsn: str | None = None
) -> None:
    """Best-effort: keep the export's phone on the customer row (empty-only — an
    existing phone is never overwritten by an import)."""
    try:
        with _connect(dsn) as conn:
            conn.execute(
                "UPDATE customers SET phone=%s WHERE tenant_id=%s AND id=%s "
                "AND (phone IS NULL OR phone='')",
                (phone, tenant_id, customer_id),
            )
    except Exception:
        pass
