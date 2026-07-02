"""SMS-3: cross-channel STOP/suppression ledger (CustomerAcq-t90.3, blueprint §2-B3/§4.2/§4.3).

The write path (ingestion) and the read path (gating views) over the
``14-suppression-consent.sql`` tables. Suppression is PERMANENT — rows are
never deleted. Ingest is idempotent: exact re-mirrors dedupe on the natural
key, a repeated STOP for an already-stopped identifier short-circuits (no
second row / supersede chain), and carrier-error retries dedupe on
``provider_sid``. Identifiers are CANONICALIZED at this boundary (E.164 for
phones, lowercased emails) so a front-desk "(702) 555-0100" and the send
path's "+17025550100" always match — formatting can never leak a send past a
revocation.

**The STOP transaction (AC #10).** A human opt-out writes THREE records in ONE
transaction: the suppression row, the consent revocation (``consent.revoked_at``
per blueprint 4.3-2), and the bi-temporal contact-preference supersede
(4.3-5). Atomicity kills the partial-STOP crash windows outright.

**Crash windows (exactly-once rigor, AC #7)** — enumerated, with the mitigation:

* W1 — ingest retried after a crash mid-call: the suppression insert is
  idempotent (natural-key UNIQUE); a retry lands on the committed row (or
  re-runs the whole atomic transaction if nothing committed). No partial state
  is observable either way.
* W2 — provider send succeeded but the process died before recording it: the
  send path must call :func:`record_send_event` with the caller's ``conn`` in
  the SAME transaction that settles the side-effect ledger row, so the
  delivery row and the SENT status commit together. PLAINLY: for SMS the
  residual provider-call→settle window is NOT covered by provider-side
  idempotency — Twilio's Messages API has no idempotency token. The SMS
  connector (SMS-1/SMS-4 wiring) MUST claim the send slot BEFORE the provider
  call (:func:`claim_send_slot`) and, on recovering a stale SENDING claim,
  check ``send_events`` by idempotency key before ever re-driving Twilio.
  What remains is at-most-once-per-claim, not blind at-least-once.
* W3 — STOP recorded but consent/memories not flipped: impossible; single
  transaction (above).
* W4 — a STOP arrives while a send is in flight: unavoidable race with
  physics; bounded by :func:`send_backstop` reading the committed ledger at
  claim time, so the window is claim→provider-call, not staging→send.
* W5/W6 — concurrent staging duplicates / cap races: owned by
  :mod:`sideeffects.staging` (DB unique index + advisory xact lock).
* W7 — two concurrent senders with DIFFERENT drafts for the same recipient
  both pass a read-only frequency check (check-then-act): closed by
  :func:`claim_send_slot`, which checks suppression + window AND consumes the
  slot in ONE serialized transaction (per-recipient advisory xact lock).
  :func:`send_backstop` alone is an ADVISORY pre-check, not the claim.

NOTE (honest scope): trunk has no SMS send path yet — these are the
enforcement primitives plus schema; the SMS connector beads (SMS-1/SMS-4)
wire them. Until then nothing consumes the window in production.

Every read helper is FAIL-CLOSED at the consumer: :func:`recipient_view`
returns ``(None, None)`` when the ledger is unreachable — which
:mod:`compliance.sms_gate` blocks on — and :func:`send_backstop` returns
``(False, ...)`` rather than guessing.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

from compliance.sms_gate import ConsentRecord, RecipientContext
from ops.tenant_guard import assert_tenant_writable

__all__ = [
    "AudienceFilterResult",
    "ConsentStatus",
    "RecipientLedgerView",
    "backfill_test_memories",
    "carrier_30007_spike",
    "claim_send_slot",
    "consent_status",
    "ensure_schema",
    "filter_audience",
    "get_memories",
    "ingest_email_unsubscribe",
    "ingest_manual_revocation",
    "ingest_twilio_opt_out",
    "is_suppressed",
    "recent_send_counts",
    "recipient_context_for_gate",
    "recipient_view",
    "record_carrier_error",
    "record_consent",
    "record_delivery_event",
    "record_preference_memory",
    "record_send_event",
    "record_suppression",
    "send_backstop",
]

log = logging.getLogger(__name__)

_INITDB = Path(__file__).resolve().parents[2] / "infra" / "initdb"
# 14 ALTERs the outbox, so the boundary schema must exist first.
_SCHEMA_SQLS = (
    _INITDB / "02-side-effect-boundary.sql",
    _INITDB / "14-suppression-consent.sql",
)

# Reasons that are a HUMAN revocation — these also revoke consent and supersede
# the contact-preference memories. Carrier errors suppress deliverability but do
# not speak for the human, so they do not revoke consent.
_REVOKING_REASONS = frozenset({"stop", "email_unsub", "web_form", "verbal", "manual"})

# Carrier error codes that permanently suppress the recipient (undeliverable /
# blocked classes). 30007 is content filtering — the recipient is fine, the
# MESSAGE is the problem — so it alerts instead (see carrier_30007_spike).
_AUTO_SUPPRESS_CODES = frozenset({30003, 30004, 30005, 30006})


def _dsn(dsn: str | None) -> str:
    return dsn or os.environ.get(
        "ENGINE_DATABASE_URL", "postgresql://scalers:scalers@localhost:5432/scalers"
    )


def _connect(dsn: str | None) -> psycopg.Connection[Any]:
    return psycopg.connect(_dsn(dsn), autocommit=True, row_factory=dict_row)


def _normalize_identifier(identifier: str) -> str:
    """Canonicalize an identifier at the ledger boundary: emails lowercase,
    US phone numbers to E.164 (``+1XXXXXXXXXX``). Everything the ledger writes
    OR reads goes through this, so "(702) 555-0100" recorded at the front desk
    matches the send path's "+17025550100" — an exact-string mismatch would be
    a fail-OPEN suppression miss."""
    s = (identifier or "").strip()
    if "@" in s:
        return s.lower()
    digits = re.sub(r"\D", "", s)
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    return s


def ensure_schema(dsn: str | None = None) -> None:
    """Apply the boundary + suppression schema (idempotent DDL, safe to re-run)."""
    with _connect(dsn) as conn:
        for sql in _SCHEMA_SQLS:
            conn.execute(sql.read_text(encoding="utf-8"))


# ── write path: suppression ingestion ────────────────────────────────────────


def record_suppression(
    *,
    tenant_id: str,
    identifier: str,
    channel: str = "all",
    reason: str,
    raw_utterance: str | None = None,
    occurred_at: datetime,
    conn: psycopg.Connection[Any] | None = None,
    dsn: str | None = None,
) -> int:
    """Record one suppression event and return its row id. Idempotent on the
    natural key. For HUMAN revocation reasons this is the atomic STOP
    transaction: suppression row + ``consent.revoked_at`` + bi-temporal
    memory supersede commit together or not at all (AC #10 / W3). Pass
    ``conn`` to join the CALLER's transaction (no commit here)."""
    assert_tenant_writable(tenant_id)
    if conn is not None:
        return _record_suppression_tx(
            conn, tenant_id, identifier, channel, reason, raw_utterance, occurred_at
        )
    with psycopg.connect(_dsn(dsn), row_factory=dict_row) as owned:  # txn, commit on exit
        return _record_suppression_tx(
            owned, tenant_id, identifier, channel, reason, raw_utterance, occurred_at
        )


def _record_suppression_tx(
    conn: psycopg.Connection[Any],
    tenant_id: str,
    identifier: str,
    channel: str,
    reason: str,
    raw_utterance: str | None,
    occurred_at: datetime,
) -> int:
    identifier = _normalize_identifier(identifier)
    row = conn.execute(
        """
        INSERT INTO suppression_ledger
            (tenant_id, identifier, channel, reason, raw_utterance, occurred_at)
        VALUES (%s,%s,%s,%s,%s,%s)
        ON CONFLICT ON CONSTRAINT suppression_event_uniq DO NOTHING
        RETURNING id
        """,
        (tenant_id, identifier, channel, reason, raw_utterance, occurred_at),
    ).fetchone()
    if row is None:
        # Already recorded — and its consent/memory writes committed with it.
        existing = conn.execute(
            "SELECT id FROM suppression_ledger WHERE tenant_id=%s AND identifier=%s"
            " AND channel=%s AND reason=%s AND occurred_at=%s",
            (tenant_id, identifier, channel, reason, occurred_at),
        ).fetchone()
        return existing["id"]
    if reason in _REVOKING_REASONS:
        _revoke_consent(conn, tenant_id, identifier, channel, reason, occurred_at)
        _supersede_preferences(conn, tenant_id, identifier, channel, reason, occurred_at)
    return row["id"]


def _revoke_consent(
    conn: psycopg.Connection[Any],
    tenant_id: str,
    identifier: str,
    channel: str,
    reason: str,
    occurred_at: datetime,
) -> None:
    conn.execute(
        "UPDATE consent SET revoked_at=%s, revoke_source=%s"
        " WHERE tenant_id=%s AND identifier=%s AND revoked_at IS NULL"
        " AND (%s = 'all' OR channel = %s)",
        (occurred_at, reason, tenant_id, identifier, channel, channel),
    )


def _supersede_preferences(
    conn: psycopg.Connection[Any],
    tenant_id: str,
    identifier: str,
    channel: str,
    reason: str,
    occurred_at: datetime,
) -> None:
    open_rows = conn.execute(
        "SELECT id, is_test FROM contact_memories WHERE tenant_id=%s AND identifier=%s"
        " AND valid_to IS NULL FOR UPDATE",
        (tenant_id, identifier),
    ).fetchall()
    # The do-not-contact supersede inherits is_test from what it supersedes: a
    # test contact's STOP must NOT inject a real do-not-contact row into recall.
    # A STOP with no prior memory is a real event (is_test=false).
    inherit_test = bool(open_rows) and all(r["is_test"] for r in open_rows)
    new = conn.execute(
        "INSERT INTO contact_memories (tenant_id, identifier, content, valid_from, is_test)"
        " VALUES (%s,%s,%s,%s,%s) RETURNING id",
        (
            tenant_id, identifier,
            Json({
                "kind": "contact_preference", "do_not_contact": True,
                "channel": channel, "reason": reason,
            }),
            occurred_at, inherit_test,
        ),
    ).fetchone()
    if open_rows:
        conn.execute(
            "UPDATE contact_memories SET valid_to=%s, superseded_by=%s WHERE id = ANY(%s)",
            (occurred_at, new["id"], [r["id"] for r in open_rows]),
        )


def ingest_twilio_opt_out(
    event: dict[str, Any],
    *,
    tenant_id: str,
    occurred_at: datetime | None = None,
    dsn: str | None = None,
) -> int | None:
    """Mirror one Twilio OptOutType webhook event into the ledger. ``STOP``
    suppresses (returns the row id); ``START``/``HELP`` are not suppressions
    (returns ``None`` — opt-back-in is deliberately NOT automated; suppression
    is permanent until an operator decides otherwise).

    Retry-idempotent: Twilio webhooks retry without a stable event timestamp,
    so a STOP for an identifier that already has an sms/all stop row is a
    no-op returning the existing row id — no duplicate row, no second
    memory-supersede chain."""
    if (event.get("OptOutType") or "").strip().upper() != "STOP":
        return None
    identifier = _normalize_identifier(event.get("From") or "")
    if not identifier:
        raise ValueError("Twilio opt-out event has no 'From' number")
    with _connect(dsn) as conn:
        existing = conn.execute(
            "SELECT id FROM suppression_ledger WHERE tenant_id=%s AND identifier=%s"
            " AND reason='stop' AND (channel='sms' OR channel='all') LIMIT 1",
            (tenant_id, identifier),
        ).fetchone()
    if existing is not None:
        return existing["id"]
    return record_suppression(
        tenant_id=tenant_id, identifier=identifier, channel="sms", reason="stop",
        raw_utterance=event.get("Body"),
        occurred_at=occurred_at or datetime.now(timezone.utc),
        dsn=dsn,
    )


def ingest_email_unsubscribe(
    *,
    tenant_id: str,
    email: str,
    occurred_at: datetime,
    raw_utterance: str | None = None,
    dsn: str | None = None,
) -> int:
    """Ingest an email unsubscribe (one-click / list-unsubscribe / manual)."""
    return record_suppression(
        tenant_id=tenant_id, identifier=email.strip().lower(), channel="email",
        reason="email_unsub", raw_utterance=raw_utterance, occurred_at=occurred_at,
        dsn=dsn,
    )


def ingest_manual_revocation(
    *,
    tenant_id: str,
    identifier: str,
    kind: str,
    occurred_at: datetime,
    raw_utterance: str | None = None,
    channel: str = "all",
    dsn: str | None = None,
) -> int:
    """Ingest a web-form / verbal / manual revocation. Defaults to
    ``channel='all'`` — a human saying "stop contacting me" via ANY reasonable
    channel revokes everywhere (FCC rule effective Apr 11 2025)."""
    if kind not in ("web_form", "verbal", "manual"):
        raise ValueError(f"unknown revocation kind {kind!r}")
    return record_suppression(
        tenant_id=tenant_id, identifier=identifier, channel=channel, reason=kind,
        raw_utterance=raw_utterance, occurred_at=occurred_at, dsn=dsn,
    )


def record_carrier_error(
    *,
    tenant_id: str,
    identifier: str,
    code: int,
    occurred_at: datetime | None = None,
    provider_sid: str | None = None,
    dsn: str | None = None,
) -> bool:
    """Record one carrier delivery error. Codes 30003-30006 (undeliverable /
    blocked) auto-suppress the recipient and return ``True``; 30007 (content
    filtering) is counted for the spike alert but does NOT suppress — the
    message, not the recipient, is the problem. The error row and the
    auto-suppress row commit in ONE transaction; a ``provider_sid`` makes
    webhook retries no-ops (unique), so the spike count stays honest."""
    assert_tenant_writable(tenant_id)
    when = occurred_at or datetime.now(timezone.utc)
    identifier = _normalize_identifier(identifier)
    suppressed = False
    with psycopg.connect(_dsn(dsn), row_factory=dict_row) as conn:  # one txn
        inserted = conn.execute(
            "INSERT INTO carrier_errors (tenant_id, identifier, code, provider_sid,"
            " occurred_at) VALUES (%s,%s,%s,%s,%s)"
            " ON CONFLICT (provider_sid) DO NOTHING RETURNING id",
            (tenant_id, identifier, code, provider_sid, when),
        ).fetchone()
        if inserted is None:
            return code in _AUTO_SUPPRESS_CODES  # retry of an already-recorded event
        if code in _AUTO_SUPPRESS_CODES:
            record_suppression(
                tenant_id=tenant_id, identifier=identifier, channel="sms",
                reason=f"carrier_{code}", occurred_at=when, conn=conn,
            )
            suppressed = True
    try:  # best-effort metric — never blocks the ledger write
        from metrics import record_carrier_error_metric

        record_carrier_error_metric(tenant=tenant_id, code=code)
    except Exception:  # noqa: BLE001
        pass
    if code == 30007:
        log.warning(
            "carrier 30007 (content filtering) for tenant=%s — check carrier_30007_spike",
            tenant_id,
        )
    return suppressed


def carrier_30007_spike(
    *,
    tenant_id: str,
    window_minutes: int = 60,
    threshold: int = 5,
    now: datetime | None = None,
    dsn: str | None = None,
) -> tuple[bool, int]:
    """``(spiking, count)`` of 30007 content-filtering errors in the window.
    A spike means the registered content / campaign is being filtered — stop
    the campaign and fix the content, don't keep blasting."""
    when = now or datetime.now(timezone.utc)
    with _connect(dsn) as conn:
        row = conn.execute(
            "SELECT count(*) AS n FROM carrier_errors"
            " WHERE tenant_id=%s AND code=30007 AND occurred_at > %s AND occurred_at <= %s",
            (tenant_id, when - timedelta(minutes=window_minutes), when),
        ).fetchone()
    count = row["n"]
    return count >= threshold, count


# ── write path: consent + memories + delivery events ─────────────────────────


def record_consent(
    *,
    tenant_id: str,
    identifier: str,
    channel: str,
    source: str,
    granted_at: datetime,
    dsn: str | None = None,
) -> int:
    """Record a PEWC consent grant with provenance (idempotent on the natural key)."""
    assert_tenant_writable(tenant_id)
    identifier = _normalize_identifier(identifier)
    with _connect(dsn) as conn:
        row = conn.execute(
            "INSERT INTO consent (tenant_id, identifier, channel, source, granted_at)"
            " VALUES (%s,%s,%s,%s,%s)"
            " ON CONFLICT ON CONSTRAINT consent_grant_uniq DO NOTHING RETURNING id",
            (tenant_id, identifier, channel, source, granted_at),
        ).fetchone()
        if row is not None:
            return row["id"]
        existing = conn.execute(
            "SELECT id FROM consent WHERE tenant_id=%s AND identifier=%s AND channel=%s"
            " AND granted_at=%s",
            (tenant_id, identifier, channel, granted_at),
        ).fetchone()
        return existing["id"]


@dataclass(frozen=True)
class ConsentStatus:
    """The latest consent grant for (tenant, identifier, channel) and whether
    it is still active (not revoked)."""

    source: str
    granted_at: datetime
    revoked_at: datetime | None
    revoke_source: str | None

    @property
    def active(self) -> bool:
        return self.revoked_at is None


def consent_status(
    *, tenant_id: str, identifier: str, channel: str, dsn: str | None = None
) -> ConsentStatus | None:
    """The most recent consent grant, or ``None`` when no row exists (which the
    sms gate treats as a hard block)."""
    identifier = _normalize_identifier(identifier)
    with _connect(dsn) as conn:
        row = conn.execute(
            "SELECT source, granted_at, revoked_at, revoke_source FROM consent"
            " WHERE tenant_id=%s AND identifier=%s AND channel=%s"
            " ORDER BY granted_at DESC LIMIT 1",
            (tenant_id, identifier, channel),
        ).fetchone()
    if row is None:
        return None
    return ConsentStatus(
        source=row["source"], granted_at=row["granted_at"],
        revoked_at=row["revoked_at"], revoke_source=row["revoke_source"],
    )


def record_preference_memory(
    *,
    tenant_id: str,
    identifier: str,
    content: dict[str, Any],
    valid_from: datetime,
    is_test: bool = False,
    dsn: str | None = None,
) -> int:
    """Record a contact-preference memory row (open-ended valid time).
    ``is_test=True`` marks a synthetic/test artifact that recall excludes by
    default (fr1.3 memory de-pollution)."""
    assert_tenant_writable(tenant_id)
    with _connect(dsn) as conn:
        row = conn.execute(
            "INSERT INTO contact_memories (tenant_id, identifier, content, valid_from,"
            " is_test) VALUES (%s,%s,%s,%s,%s) RETURNING id",
            (tenant_id, identifier, Json(content), valid_from, is_test),
        ).fetchone()
        return row["id"]


def get_memories(
    *, tenant_id: str, identifier: str, include_test: bool = False, dsn: str | None = None
) -> list[dict[str, Any]]:
    """A contact's memory rows (superseded history included — bi-temporal, the
    past stays auditable). Recall EXCLUDES ``is_test`` artifacts by default so a
    ``test_mem_*`` row can never ground a real draft; pass ``include_test=True``
    for audit/backfill views."""
    sql = (
        "SELECT id, content, valid_from, valid_to, superseded_by, recorded_at, is_test"
        " FROM contact_memories WHERE tenant_id=%s AND identifier=%s"
    )
    if not include_test:
        sql += " AND is_test = false"
    sql += " ORDER BY id"
    with _connect(dsn) as conn:
        return conn.execute(sql, (tenant_id, identifier)).fetchall()


def backfill_test_memories(
    *, tenant_id: str | None = None, dsn: str | None = None
) -> int:
    """FLAG (never delete) contact-preference memories that are ``test_mem_*``
    artifacts — by identifier prefix, a ``test_mem_*`` content source, or a
    ``kind='test'`` marker — setting ``is_test=true``. Returns the number of
    rows newly flagged (idempotent: an already-flagged row is not recounted).
    This is the mechanism that clears the audit's 102 injected test memories."""
    # '%%' escapes the LIKE wildcard so psycopg does not read it as a placeholder.
    where = (
        "is_test = false AND ("
        "identifier LIKE 'test_mem_%%'"
        " OR content->>'source' LIKE 'test_mem_%%'"
        " OR content->>'kind' = 'test')"
    )
    params: list[Any] = []
    if tenant_id is not None:
        where += " AND tenant_id = %s"
        params.append(tenant_id)
    with _connect(dsn) as conn:
        return conn.execute(
            f"UPDATE contact_memories SET is_test = true WHERE {where}", params
        ).rowcount


def recent_send_counts(
    *,
    tenant_id: str,
    identifiers: Sequence[str],
    channel: str = "sms",
    kind: str = "promo",
    window_hours: int = 72,
    now: datetime | None = None,
    dsn: str | None = None,
) -> dict[str, int]:
    """Per-identifier count of recent ``kind`` sends in the window, read from
    ``send_events`` — the batch read backing the queue's per-recipient
    frequency awareness (READ-only; no dedupe here). Every requested identifier
    appears in the result (0 when it has no sends), keyed by the ORIGINAL
    identifier the caller passed."""
    when = now or datetime.now(timezone.utc)
    if not identifiers:
        return {}
    canon = {i: _normalize_identifier(i) for i in identifiers}
    with _connect(dsn) as conn:
        rows = conn.execute(
            "SELECT identifier, count(*) AS n FROM send_events"
            " WHERE tenant_id=%s AND identifier = ANY(%s) AND channel=%s AND kind=%s"
            " AND occurred_at > %s AND occurred_at <= %s GROUP BY identifier",
            (tenant_id, list(set(canon.values())), channel, kind,
             when - timedelta(hours=window_hours), when),
        ).fetchall()
    counts = {r["identifier"]: r["n"] for r in rows}
    return {orig: counts.get(canon[orig], 0) for orig in identifiers}


def record_send_event(
    *,
    tenant_id: str,
    identifier: str,
    channel: str,
    mode: str,
    kind: str = "promo",
    idempotency_key: str | None = None,
    occurred_at: datetime | None = None,
    conn: psycopg.Connection[Any] | None = None,
    dsn: str | None = None,
) -> int:
    """Record one delivery attempt. TEST-MODE sends record here too
    (``mode='test_redirect'``) — the sandbox proves the machinery and consumes
    the recipient's frequency window like a real send.

    Pass ``conn`` to write inside the CALLER's transaction — the send path does
    this in the same transaction that settles the side-effect ledger row, so
    the delivery record and the SENT status commit together (W2)."""
    assert_tenant_writable(tenant_id)
    identifier = _normalize_identifier(identifier)
    sql = (
        "INSERT INTO send_events"
        " (tenant_id, identifier, channel, kind, mode, idempotency_key, occurred_at)"
        " VALUES (%s,%s,%s,%s,%s,%s, COALESCE(%s, now()))"
        " ON CONFLICT ON CONSTRAINT send_events_key_uniq DO NOTHING RETURNING id"
    )
    params = (tenant_id, identifier, channel, kind, mode, idempotency_key, occurred_at)
    lookup = "SELECT id FROM send_events WHERE idempotency_key=%s"

    def _write(c: psycopg.Connection[Any]) -> int:
        row = c.execute(sql, params).fetchone()
        if row is not None:
            return row[0] if not isinstance(row, dict) else row["id"]
        existing = c.execute(lookup, (idempotency_key,)).fetchone()
        return existing[0] if not isinstance(existing, dict) else existing["id"]

    if conn is not None:
        return _write(conn)
    with _connect(dsn) as owned:
        return _write(owned)


def record_delivery_event(
    *,
    tenant_id: str,
    identifier: str,
    status: str,
    channel: str = "sms",
    provider_sid: str | None = None,
    error_code: int | None = None,
    raw: dict[str, Any] | None = None,
    occurred_at: datetime | None = None,
    dsn: str | None = None,
) -> int:
    """Record one provider status callback (§4.3-10). Idempotent per
    (provider_sid, status) so webhook retries are no-ops. Written for sandbox
    (redirected) sends too — the delivery machinery is proven before go-live."""
    assert_tenant_writable(tenant_id)
    identifier = _normalize_identifier(identifier)
    with _connect(dsn) as conn:
        row = conn.execute(
            "INSERT INTO delivery_events"
            " (tenant_id, identifier, channel, provider_sid, status, error_code, raw,"
            "  occurred_at)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s, COALESCE(%s, now()))"
            " ON CONFLICT (provider_sid, status) DO NOTHING RETURNING id",
            (tenant_id, identifier, channel, provider_sid, status, error_code,
             Json(raw) if raw is not None else None, occurred_at),
        ).fetchone()
        if row is not None:
            return row["id"]
        existing = conn.execute(
            "SELECT id FROM delivery_events WHERE provider_sid=%s AND status=%s",
            (provider_sid, status),
        ).fetchone()
        return existing["id"]


# ── read path: the gating views (fail-closed at every consumer) ──────────────


def is_suppressed(
    *, tenant_id: str, identifier: str, channel: str, dsn: str | None = None
) -> bool:
    """True when any ledger row suppresses this identifier on ``channel`` —
    a row for the channel itself OR a cross-channel ``'all'`` row."""
    identifier = _normalize_identifier(identifier)
    with _connect(dsn) as conn:
        row = conn.execute(
            "SELECT 1 FROM suppression_ledger WHERE tenant_id=%s AND identifier=%s"
            " AND (channel=%s OR channel='all') LIMIT 1",
            (tenant_id, identifier, channel),
        ).fetchone()
    return row is not None


@dataclass(frozen=True)
class RecipientLedgerView:
    """The ledger's answer for one recipient, shaped for
    :class:`compliance.sms_gate.RecipientContext`: ``None`` fields mean the
    ledger could not be consulted — the gate fails closed on them."""

    suppressed: bool | None
    recent_promo_sends: tuple[datetime, ...] | None


def recipient_view(
    *,
    tenant_id: str,
    identifier: str,
    channel: str = "sms",
    window_hours: int = 72,
    now: datetime | None = None,
    dsn: str | None = None,
) -> RecipientLedgerView:
    """Materialize the two ledger fields the sms gate consumes. NEVER raises:
    an unreachable ledger returns ``(None, None)`` and the gate blocks."""
    when = now or datetime.now(timezone.utc)
    identifier = _normalize_identifier(identifier)
    try:
        with _connect(dsn) as conn:
            sup = conn.execute(
                "SELECT 1 FROM suppression_ledger WHERE tenant_id=%s AND identifier=%s"
                " AND (channel=%s OR channel='all') LIMIT 1",
                (tenant_id, identifier, channel),
            ).fetchone()
            sends = conn.execute(
                "SELECT occurred_at FROM send_events"
                " WHERE tenant_id=%s AND identifier=%s AND channel=%s AND kind='promo'"
                " AND occurred_at > %s AND occurred_at <= %s ORDER BY occurred_at",
                (tenant_id, identifier, channel, when - timedelta(hours=window_hours), when),
            ).fetchall()
    except Exception as exc:  # noqa: BLE001 — fail closed, never guess
        log.warning("suppression ledger unreachable (%s) — failing closed", exc)
        return RecipientLedgerView(suppressed=None, recent_promo_sends=None)
    return RecipientLedgerView(
        suppressed=sup is not None,
        recent_promo_sends=tuple(r["occurred_at"] for r in sends),
    )


def recipient_context_for_gate(
    *,
    tenant_id: str,
    phone: str,
    channel: str = "sms",
    studio_timezone: str | None = None,
    window_hours: int = 72,
    now: datetime | None = None,
    dsn: str | None = None,
) -> RecipientContext:
    """Build the sms gate's :class:`RecipientContext` entirely from the ledger
    + consent tables — the ONE wiring point between storage and the pure gate.
    After a STOP, the consent check AND the suppression check each block
    (AC #10 defense-in-depth)."""
    view = recipient_view(
        tenant_id=tenant_id, identifier=phone, channel=channel,
        window_hours=window_hours, now=now, dsn=dsn,
    )
    consent: ConsentRecord | None
    try:
        status = consent_status(
            tenant_id=tenant_id, identifier=phone, channel=channel, dsn=dsn
        )
    except Exception:  # noqa: BLE001 — unreadable consent = no consent (fail closed)
        status = None
    if status is None:
        consent = None
    else:
        consent = ConsentRecord(
            phone=phone, sms_opt_in=status.active, source=status.source,
            granted_at=status.granted_at,
        )
    return RecipientContext(
        phone=phone, consent=consent, suppressed=view.suppressed,
        recent_promo_sends=view.recent_promo_sends, studio_timezone=studio_timezone,
    )


def send_backstop(
    *,
    tenant_id: str,
    identifier: str,
    channel: str = "sms",
    kind: str = "promo",
    max_sends: int = 1,
    window_hours: int = 72,
    now: datetime | None = None,
    dsn: str | None = None,
) -> tuple[bool, str]:
    """SEND-TIME backstop — ``(ok, reason)``, called at the moment of claim
    (eligibility/publish) so a STOP or an earlier send that arrived AFTER
    staging still blocks. DND/STOP is permanent; the frequency window
    (default 1 promo / 72h) counts every delivery event, redirected sandbox
    sends included. Fail-closed: an unreachable ledger blocks.

    ADVISORY pre-check only (read-only, check-then-act): two concurrent
    callers can both pass it. The atomic form that actually guards the wire
    is :func:`claim_send_slot` (W7) — call THAT at the moment of send."""
    when = now or datetime.now(timezone.utc)
    identifier = _normalize_identifier(identifier)
    try:
        with _connect(dsn) as conn:
            sup = conn.execute(
                "SELECT reason, channel FROM suppression_ledger"
                " WHERE tenant_id=%s AND identifier=%s AND (channel=%s OR channel='all')"
                " ORDER BY occurred_at LIMIT 1",
                (tenant_id, identifier, channel),
            ).fetchone()
            if sup is not None:
                return False, (
                    f"suppressed (reason={sup['reason']}, channel={sup['channel']})"
                    " — permanent"
                )
            row = conn.execute(
                "SELECT count(*) AS n FROM send_events"
                " WHERE tenant_id=%s AND identifier=%s AND channel=%s AND kind=%s"
                " AND occurred_at > %s AND occurred_at <= %s",
                (tenant_id, identifier, channel, kind,
                 when - timedelta(hours=window_hours), when),
            ).fetchone()
    except Exception as exc:  # noqa: BLE001
        return False, f"suppression ledger unavailable — fail closed ({exc})"
    if row["n"] >= max_sends:
        return False, (
            f"frequency window: {row['n']} {kind} send(s) in the last"
            f" {window_hours}h (cap {max_sends})"
        )
    return True, "ok"


def claim_send_slot(
    *,
    tenant_id: str,
    identifier: str,
    channel: str = "sms",
    kind: str = "promo",
    mode: str,
    idempotency_key: str | None = None,
    max_sends: int = 1,
    window_hours: int = 72,
    now: datetime | None = None,
    dsn: str | None = None,
) -> tuple[bool, str]:
    """ATOMIC send-slot claim (W7): checks suppression + the frequency window
    AND records the delivery event in ONE transaction, serialized per
    (tenant, identifier, channel) by an advisory xact lock — two concurrent
    senders can never both pass. Call this at the moment of send, BEFORE the
    provider call: a claimed slot with a failed provider send under-sends
    (fail-closed), never over-sends. Returns ``(ok, reason)``; a refused claim
    writes nothing. Fail-closed: an unreachable ledger refuses the claim."""
    assert_tenant_writable(tenant_id)  # config error — must surface, not be swallowed below
    when = now or datetime.now(timezone.utc)
    identifier = _normalize_identifier(identifier)
    try:
        with psycopg.connect(_dsn(dsn), row_factory=dict_row) as conn:  # one txn
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"send-slot:{tenant_id}:{identifier}:{channel}",),
            )
            sup = conn.execute(
                "SELECT reason, channel FROM suppression_ledger"
                " WHERE tenant_id=%s AND identifier=%s AND (channel=%s OR channel='all')"
                " ORDER BY occurred_at LIMIT 1",
                (tenant_id, identifier, channel),
            ).fetchone()
            if sup is not None:
                conn.rollback()
                return False, (
                    f"suppressed (reason={sup['reason']}, channel={sup['channel']})"
                    " — permanent"
                )
            n = conn.execute(
                "SELECT count(*) AS n FROM send_events"
                " WHERE tenant_id=%s AND identifier=%s AND channel=%s AND kind=%s"
                " AND occurred_at > %s AND occurred_at <= %s",
                (tenant_id, identifier, channel, kind,
                 when - timedelta(hours=window_hours), when),
            ).fetchone()["n"]
            if n >= max_sends:
                conn.rollback()
                return False, (
                    f"frequency window: {n} {kind} send(s) in the last"
                    f" {window_hours}h (cap {max_sends})"
                )
            conn.execute(
                "INSERT INTO send_events"
                " (tenant_id, identifier, channel, kind, mode, idempotency_key, occurred_at)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s)"
                " ON CONFLICT ON CONSTRAINT send_events_key_uniq DO NOTHING",
                (tenant_id, identifier, channel, kind, mode, idempotency_key, when),
            )
    except Exception as exc:  # noqa: BLE001
        return False, f"suppression ledger unavailable — fail closed ({exc})"
    return True, "ok"


@dataclass(frozen=True)
class AudienceFilterResult:
    """Suppression-filtered audience with honest counts:
    ``len(kept) + len(removed)`` always equals the input size."""

    kept: tuple[str, ...]
    removed: tuple[tuple[str, str], ...]  # (identifier, reason)


def filter_audience(
    *,
    tenant_id: str,
    identifiers: Sequence[str],
    channel: str,
    dsn: str | None = None,
) -> AudienceFilterResult:
    """Filter suppressed contacts out BEFORE audience creation, so recipient
    counts are honest — never rely on provider-side DND at send time alone.
    Kept/removed carry the CALLER's original identifiers; matching happens on
    the canonical form."""
    if not identifiers:
        return AudienceFilterResult(kept=(), removed=())
    canonical = {i: _normalize_identifier(i) for i in identifiers}
    with _connect(dsn) as conn:
        rows = conn.execute(
            "SELECT DISTINCT ON (identifier) identifier, reason, channel"
            " FROM suppression_ledger WHERE tenant_id=%s AND identifier = ANY(%s)"
            " AND (channel=%s OR channel='all') ORDER BY identifier, occurred_at",
            (tenant_id, list(set(canonical.values())), channel),
        ).fetchall()
    blocked = {
        r["identifier"]: f"suppressed (reason={r['reason']}, channel={r['channel']})"
        for r in rows
    }
    kept = tuple(i for i in identifiers if canonical[i] not in blocked)
    removed = tuple((i, blocked[canonical[i]]) for i in identifiers if canonical[i] in blocked)
    return AudienceFilterResult(kept=kept, removed=removed)
