"""Ink Pulse lead ingestion — the pre-CRM consultation feed (client direction,
PA meeting 2026-07-11).

The client's studio talks to prospects in "Ink Pulse" BEFORE they ever reach the
CRM — the CRM only holds booked / deposit-pending clients, so the consultation
leads that go quiet (name / email / phone / instagram / conversation history) are
invisible to the campaign engine today. This module ingests that export so those
leads become real, targetable customers.

Two honest layers:

  * :func:`parse_ink_pulse_export` — PURE (no DB, no network): normalizes an Ink
    Pulse CSV/JSON export into the ``customers`` lead-row shape the existing
    :func:`studio.customer_research.upsert_lead` accepts. Contact fields are kept
    VERBATIM; a row with no way to reach the person (no email/phone/instagram) is
    dropped, never invented. Every row is tagged ``lead_stage="ink_pulse"`` +
    ``source="ink_pulse"`` so a pre-CRM consultation lead is always distinguishable
    from a booked CRM client downstream (a named-cohort / consent gate can key on
    it).
  * :func:`ingest_ink_pulse` — DB-backed UPSERT (:func:`_upsert_ink_pulse_lead`) that
    dedups on ANY contact handle the lead carries — (tenant,email) OR (tenant,phone)
    OR (tenant,ig_handle) — persisting phone/Instagram and the ``ink_pulse`` source so
    a phone/IG-only quiet lead is neither dropped nor duplicated on re-ingest.

Location: the export's city/location flows straight into ``customers.city`` so the
campaign can target by CUSTOMER location, not just the studio — the exact gap the
client raised. Where a lead has no city on file, :func:`studio.location` resolves
it (on-file first, web research second) rather than assuming the studio's city.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any

SOURCE_INK_PULSE = "ink_pulse"

# Header aliases the Ink Pulse export may use → the normalized lead-row key.
_FIELD_ALIASES: dict[str, str] = {
    "name": "name", "customer_name": "name", "full_name": "name", "lead_name": "name",
    "email": "email", "customer_email": "email", "e-mail": "email",
    "phone": "phone", "customer_phone": "phone", "mobile": "phone", "number": "phone",
    "instagram": "ig_handle", "ig": "ig_handle", "ig_handle": "ig_handle",
    "instagram_handle": "ig_handle", "handle": "ig_handle",
    "city": "location", "location": "location", "town": "location",
    "conversation": "conversation", "messages": "conversation", "thread": "conversation",
    "notes": "conversation", "history": "conversation", "last_message": "conversation",
    "interests": "interests", "interest": "interests", "style": "interests",
    "artist": "artist", "shop": "shop",
}

# A row must carry at least one of these to be reachable — else it is dropped.
_CONTACT_KEYS = ("email", "phone", "ig_handle")

# The consultation-specific HEADERS that separate an Ink Pulse export from a plain
# customer list. Deliberately excludes the ambiguous "notes" (a generic CRM column):
# detection keys on an unmistakable consultation-thread / interests header so a
# name,email,phone,notes customer CSV is never stolen into the ink-pulse path.
_CONSULTATION_HEADERS = frozenset({
    "conversation", "messages", "thread", "history", "last_message",
    "interests", "interest", "style",
})


def _norm_header(h: str) -> str:
    return (h or "").strip().lstrip("﻿").lower().replace(" ", "_")


def _header_keys(content: str) -> set[str]:
    """The normalized column keys of the export (CSV header or JSON object keys)."""
    text = (content or "").lstrip("﻿").strip()
    if not text:
        return set()
    if text.startswith("["):
        try:
            data = json.loads(text)
        except Exception:
            return set()
        first = next((r for r in data if isinstance(r, dict)), None) if isinstance(data, list) else None
        return {_norm_header(k) for k in (first or {})}
    try:
        row = next(csv.reader(io.StringIO(text)))
    except Exception:
        return set()
    return {_norm_header(c) for c in row}


def looks_like_ink_pulse(content: str) -> bool:
    """Header-shape detection: an Ink Pulse export names a contact field
    (email/phone/instagram) AND a CONSULTATION signal (a conversation/thread column
    or an interests/style note).

    The consultation signal is the load-bearing discriminator: it separates an Ink
    Pulse export from a plain ``name,email,phone,city`` customer list (which also has
    a contact field and a name, but no consultation thread) — so this detector NEVER
    steals a generic customer upload into the ink-pulse path. It also excludes a
    competitor export (handle+metrics, no contact) and a bare address list."""
    cols = _header_keys(content)
    mapped = {_FIELD_ALIASES.get(c) for c in cols}
    has_contact = bool(mapped & {"email", "phone", "ig_handle"})
    has_consultation = bool(cols & _CONSULTATION_HEADERS)
    return has_contact and has_consultation


def _raw_rows(content: str) -> list[dict[str, str]]:
    text = (content or "").lstrip("﻿").strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            data = json.loads(text)
        except Exception:
            return []
        return [
            {str(k): ("" if v is None else str(v)) for k, v in r.items()}
            for r in (data if isinstance(data, list) else [])
            if isinstance(r, dict)
        ]
    reader = csv.DictReader(io.StringIO(text))
    return [{(k or ""): (v or "") for k, v in r.items() if k} for r in reader]


def parse_ink_pulse_export(content: str) -> list[dict[str, Any]]:
    """PURE normalization of an Ink Pulse export into lead rows for
    :func:`studio.customer_research.upsert_lead`.

    Each returned row carries the normalized contact/location fields, the verbatim
    conversation snippet folded into ``notes`` (so the researcher / psych analyst
    read the REAL thread), and the ``lead_stage`` / ``source`` = ``"ink_pulse"``
    markers. Rows with no reachable contact handle are dropped (never invented).
    Values are kept verbatim — nothing is rewritten."""
    out: list[dict[str, Any]] = []
    for raw in _raw_rows(content):
        norm: dict[str, str] = {}
        for k, v in raw.items():
            key = _FIELD_ALIASES.get(_norm_header(k))
            val = (v or "").strip()
            if not key or not val:
                continue
            # First alias wins for a given normalized key (stable, no clobber).
            norm.setdefault(key, val)
        if not any(norm.get(k) for k in _CONTACT_KEYS):
            continue  # unreachable lead — dropped, never fabricated
        conversation = norm.pop("conversation", "")
        # The verbatim consultation thread is the note (contact fields land in
        # their own columns via the upsert, so they are NOT duplicated here).
        notes = f"Ink Pulse consultation: {conversation}" if conversation else ""
        row: dict[str, Any] = {
            "name": norm.get("name", ""),
            "email": norm.get("email", ""),
            "phone": norm.get("phone", ""),
            "ig_handle": norm.get("ig_handle", "").lstrip("@"),
            "location": norm.get("location", ""),
            "interests": norm.get("interests", ""),
            "artist": norm.get("artist", ""),
            "shop": norm.get("shop", ""),
            "notes": notes,
            "lead_stage": SOURCE_INK_PULSE,
            "source": SOURCE_INK_PULSE,
        }
        out.append(row)
    return out


def _upsert_ink_pulse_lead(
    tenant_id: str, row: dict[str, Any], *, dsn: str | None = None
) -> dict[str, Any]:
    """UPSERT one Ink Pulse lead into ``customers``, idempotent on ANY provided
    contact handle — (tenant, email) OR (tenant, phone) OR (tenant, ig_handle).

    The generic :func:`studio.customer_research.upsert_lead` dedups on email ONLY
    and never persists phone / ig_handle — wrong for Ink Pulse, whose pre-CRM
    consultation leads are frequently phone/Instagram-only (re-ingesting one would
    duplicate it, and its number/handle would be lost). This writes the real
    contact columns, stamps ``source`` + ``lead_stage`` = ``"ink_pulse"``, and
    backfills NULLs on a match without clobbering existing ground truth. Consent is
    conservative: BOTH ``email_opt_in`` and ``sms_opt_in`` default False. An Ink Pulse
    lead is a pre-CRM prospect who went QUIET — merely having emailed the studio once
    is NOT marketing consent, so the lead is ingested/enriched/targetable but a real
    email or SMS send still requires an explicit opt-in (the send-safety + named-cohort
    gates enforce this downstream regardless).

    RACE-SAFE: dedup is a pre-SELECT, so two concurrent ingests of the same lead (a
    double-clicked upload) could both miss and both INSERT. Two defenses: (1) partial
    UNIQUE backstops on (tenant, phone) / (tenant, lower(ig_handle)) — the same DB-
    boundary discipline the email path already has — and (2) an INSERT that catches
    ``UniqueViolation``, re-finds the racing writer's row, and converts to a match
    (self-heal) instead of crashing or duplicating."""
    import uuid

    import psycopg
    from psycopg.rows import dict_row

    from studio.customer_research import _dsn, ensure_lead_columns
    from studio.location import resolve_customer_location

    email = (row.get("email") or "").strip() or None
    phone = (row.get("phone") or "").strip() or None
    # Instagram handles are case-insensitive, so normalize to lowercase for BOTH the
    # stored value and the dedup match — else "@Keebs" then "keebs" is the same person
    # ingested twice. (Email already dedups case-insensitively; phone is exact/best-
    # effort since formats — "+1 555…" vs "555…" — can't be canonicalized safely.)
    ig = (row.get("ig_handle") or "").strip().lstrip("@").lower() or None
    name = (row.get("name") or "").strip() or None
    loc = resolve_customer_location({"location": row.get("location")})
    city, state = (loc["city"] or None), (loc["state"] or None)
    interests_raw = row.get("interests") or ""
    interests = [s.strip() for s in interests_raw.replace(",", ";").split(";") if s.strip()]
    notes = (row.get("notes") or "").strip() or None

    def _backfill(conn: Any, row_id: str) -> None:
        """Backfill NULL/empty columns on the matched row — including ``email``, so a
        phone-matched lead that NOW carries an email becomes findable by email later
        (else a future email-only export of the same person duplicates them). The
        email backfill can trip customers_tenant_email_uniq when a DIFFERENT row
        already owns that address — that is a genuine cross-person collision, so we
        retry keeping everything EXCEPT the email (never clobber, never crash)."""
        sql = """
            UPDATE customers SET
                name       = COALESCE(NULLIF(name, ''), %s),
                email      = COALESCE(email, %s),
                phone      = COALESCE(phone, %s),
                ig_handle  = COALESCE(ig_handle, %s),
                city       = COALESCE(city, %s),
                state      = COALESCE(state, %s),
                interests  = CASE WHEN interests IS NULL OR cardinality(interests) = 0
                                  THEN %s ELSE interests END,
                notes      = COALESCE(notes, %s),
                source     = COALESCE(source, %s),
                lead_stage = COALESCE(lead_stage, %s)
            WHERE id = %s
        """
        try:
            conn.execute(sql, (name, email, phone, ig, city, state, interests,
                               notes, SOURCE_INK_PULSE, SOURCE_INK_PULSE, row_id))
        except psycopg.errors.UniqueViolation:
            conn.execute(sql, (name, None, phone, ig, city, state, interests,
                               notes, SOURCE_INK_PULSE, SOURCE_INK_PULSE, row_id))

    ensure_lead_columns(dsn)
    _ensure_contact_unique_indexes(dsn)
    with psycopg.connect(_dsn(dsn), autocommit=True, row_factory=dict_row) as conn:
        existing = _find_existing(conn, tenant_id, email=email, phone=phone, ig=ig)
        if existing is not None:
            _backfill(conn, existing["id"])
            return {"customer_id": existing["id"], "created": False}

        cust_id = "cust_" + uuid.uuid4().hex[:16]
        try:
            conn.execute(
                """
                INSERT INTO customers
                    (id, tenant_id, name, email, phone, ig_handle, city, state,
                     interests, preferred_channels, email_opt_in, sms_opt_in,
                     source, notes, lead_stage)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (cust_id, tenant_id, name, email, phone, ig, city, state,
                 interests, [], False, False,  # opt-ins default OFF — quiet lead, no consent
                 SOURCE_INK_PULSE, notes, SOURCE_INK_PULSE),
            )
        except psycopg.errors.UniqueViolation:
            # A concurrent ingest won the INSERT race — adopt its row (self-heal).
            racer = _find_existing(conn, tenant_id, email=email, phone=phone, ig=ig)
            if racer is None:
                raise  # violation not explainable by our handles — surface it
            _backfill(conn, racer["id"])
            return {"customer_id": racer["id"], "created": False}
        return {"customer_id": cust_id, "created": True}


def _find_existing(
    conn: Any, tenant_id: str, *, email: str | None, phone: str | None, ig: str | None
) -> dict[str, Any] | None:
    """The tenant's existing row matching ANY provided contact handle, else None.
    Absent handles contribute no clause (never a false match)."""
    clauses: list[str] = []
    params: list[Any] = [tenant_id]
    if email:
        clauses.append("lower(email) = lower(%s)")
        params.append(email)
    if phone:
        clauses.append("phone = %s")
        params.append(phone)
    if ig:
        clauses.append("lower(ig_handle) = %s")  # callers pass ig pre-lowercased
        params.append(ig)
    if not clauses:
        return None
    return conn.execute(
        f"SELECT id FROM customers WHERE tenant_id = %s AND ({' OR '.join(clauses)}) LIMIT 1",
        tuple(params),
    ).fetchone()


_contact_indexes_ensured: set[str] = set()


def _ensure_contact_unique_indexes(dsn: str | None = None) -> None:
    """Idempotently add the phone / instagram UNIQUE backstops to ``customers`` —
    the DB-boundary guarantee that a pre-SELECT race can never yield two rows for
    one person (mirrors customers_tenant_email_uniq for email). Partial + non-empty
    predicates so legacy ''-valued rows never block creation. Best-effort and
    memoized like :func:`studio.customer_research.ensure_lead_columns`: a legacy DB
    with pre-existing duplicates degrades to the INSERT self-heal, never crashes."""
    import psycopg

    from studio.customer_research import _dsn

    key = _dsn(dsn)
    if key in _contact_indexes_ensured:
        return
    try:
        with psycopg.connect(key, autocommit=True) as conn:
            # Phone backstop scoped to ink_pulse-sourced rows ONLY — other import
            # paths also write phone, and two real customers can share one number
            # (a couple booking together); a global unique index would crash them.
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS customers_tenant_phone_inkpulse_uniq "
                "ON customers (tenant_id, phone) "
                "WHERE phone IS NOT NULL AND phone <> '' AND source = 'ink_pulse'"
            )
            # IG backstop is global: only ink_pulse writes ig_handle, and an
            # Instagram handle identifies exactly one account.
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS customers_tenant_ig_uniq "
                "ON customers (tenant_id, lower(ig_handle)) "
                "WHERE ig_handle IS NOT NULL AND ig_handle <> ''"
            )
        _contact_indexes_ensured.add(key)
    except Exception:
        pass  # no table yet / legacy dupes — the self-heal still prevents new dupes


def ingest_ink_pulse(
    tenant_id: str, content: str, *, dsn: str | None = None
) -> dict[str, Any]:
    """Parse + idempotently UPSERT an Ink Pulse export into ``customers``. Returns
    honest counts:

        {"ok", "rows", "ingested", "created", "matched", "customer_ids", "errors"}

    Idempotent on any contact handle (email / phone / instagram), so re-ingesting
    the same export creates nothing new. An export with no reachable rows returns
    zero counts (nothing invented).

    PARTIAL-FAILURE HONESTY: each row commits independently (autocommit), so a
    failure on row k must NOT be reported as "nothing was stored" — rows 1..k-1 are
    already durably in. A failing row is recorded in ``errors`` (row index + name +
    real error) and the batch continues; ``ok`` is True only when every row landed.
    The operator therefore sees exactly what was stored, and a retry of the same
    export is safe (the survivors just match — never duplicate)."""
    rows = parse_ink_pulse_export(content)
    if not rows:
        return {"ok": True, "rows": 0, "ingested": 0, "created": 0,
                "matched": 0, "customer_ids": [], "errors": []}
    created = matched = 0
    ids: list[str] = []
    errors: list[dict[str, Any]] = []
    for idx, row in enumerate(rows):
        try:
            res = _upsert_ink_pulse_lead(tenant_id, row, dsn=dsn)
        except Exception as exc:  # noqa: BLE001 — one bad row never voids the batch
            errors.append({"row": idx, "name": row.get("name", ""),
                           "error": f"{type(exc).__name__}: {exc}"})
            continue
        ids.append(res["customer_id"])
        if res["created"]:
            created += 1
        else:
            matched += 1
    return {
        "ok": not errors, "rows": len(rows), "ingested": len(ids),
        "created": created, "matched": matched, "customer_ids": ids,
        "errors": errors,
    }


def ink_pulse_enabled(tenant_id: str) -> bool:
    """Whether the tenant's ``[ink_pulse]`` pack config opts into the feed.
    Best-effort: no/corrupt pack → False (never a surprise ingest)."""
    try:
        from config.loader import load_pack

        cfg = getattr(load_pack(tenant_id), "ink_pulse", None)
        return bool(cfg is not None and getattr(cfg, "enabled", False))
    except Exception:
        return False
