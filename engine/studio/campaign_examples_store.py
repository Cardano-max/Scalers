"""Campaign Example Library (SD-MEMORY, CustomerAcq-ju1.2) — campaign memory.

The client's REAL past campaigns (operator-provided screenshots of their current SMS
platform, manually transcribed into ``client-data/campaign-examples.json``) stored as a
tenant-scoped, queryable example library + a deterministic pattern summary, so the
generator/supervisor ground new drafts in what this client actually sends and what
actually delivered — instead of inventing style.

Honesty contract:
  * every stored example is badged ``source='operator_screenshot'`` — transcribed, not
    invented; the file-level ``_provenance`` block is stored verbatim on each row;
  * a field that was not visible in the screenshot is null in the JSON and STAYS null —
    nothing is inferred; ``sent_at``/``scheduled_for`` are kept as transcribed TEXT
    (parsing "GMT+5" into a timestamp would be interpretation);
  * an unknown JSON field is REPORTED in the import summary, never silently dropped
    (the ju1.1 audit posture);
  * pattern extraction is pure code (keyless-safe, zero model calls); a pattern row is
    emitted ONLY with non-empty evidence example ids — never a fabricated pattern;
  * these are STYLE references, not send lists — recipient/delivered counts are
    historical facts, not targets.

Example ids are deterministic (``cex_`` + sha1(tenant|campaign_name)), so re-ingest is
idempotent and ju1.4's draft-grounding citations stay stable across runs.

Mirrors ``studio/client_import.py`` / ``studio/blueprint_store.py``: lazy psycopg,
autocommit, idempotent runtime DDL twinned by ``infra/initdb/14-campaign-examples.sql``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

SOURCE_OPERATOR_SCREENSHOT = "operator_screenshot"

_DEFAULT_DSN = "postgresql://scalers:scalers@localhost:5432/scalers"

# Every field the transcription carries (the screenshot's visible facts). An example's
# key outside this list is REPORTED as unknown — nothing is silently dropped.
_EXAMPLE_COLS: tuple[str, ...] = (
    "campaign_name", "follow_up_to", "status", "sent_at", "scheduled_for",
    "artist_name", "offer_price_usd", "offer_type", "recipient_count",
    "delivered_count", "sent_pending_count", "failed_count", "dnd_blocked_count",
    "message_copy", "message_chars", "cta", "opt_out_text", "payment_plans",
    "from_number", "attachment_present", "attachment_note", "categories",
    "artists_selected", "location", "source_screenshot",
)
_JSONB_COLS = frozenset({"categories", "artists_selected"})


def _dsn(dsn: str | None) -> str:
    return (dsn or os.environ.get("ENGINE_DATABASE_URL")
            or os.environ.get("DATABASE_URL") or _DEFAULT_DSN)


def _connect(dsn: str | None):
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(_dsn(dsn), row_factory=dict_row, autocommit=True)


_DDL = """
CREATE TABLE IF NOT EXISTS campaign_examples (
    id                 TEXT PRIMARY KEY,
    tenant_id          TEXT NOT NULL,
    campaign_name      TEXT NOT NULL,
    follow_up_to       TEXT,
    status             TEXT,
    sent_at            TEXT,
    scheduled_for      TEXT,
    artist_name        TEXT,
    offer_price_usd    NUMERIC,
    offer_type         TEXT,
    recipient_count    INTEGER,
    delivered_count    INTEGER,
    sent_pending_count INTEGER,
    failed_count       INTEGER,
    dnd_blocked_count  INTEGER,
    message_copy       TEXT,
    message_chars      INTEGER,
    cta                TEXT,
    opt_out_text       TEXT,
    payment_plans      TEXT,
    from_number        TEXT,
    attachment_present BOOLEAN,
    attachment_note    TEXT,
    categories         JSONB,
    artists_selected   JSONB,
    location           TEXT,
    source_screenshot  TEXT,
    source             TEXT NOT NULL DEFAULT 'operator_screenshot',
    provenance         JSONB,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, campaign_name)
);
CREATE INDEX IF NOT EXISTS idx_campaign_examples_tenant_artist
    ON campaign_examples (tenant_id, artist_name);
CREATE TABLE IF NOT EXISTS campaign_example_patterns (
    id                   TEXT PRIMARY KEY,
    tenant_id            TEXT NOT NULL,
    pattern_key          TEXT NOT NULL,
    description          TEXT NOT NULL,
    evidence_example_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    detail               JSONB,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, pattern_key)
);
"""


def ensure_schema(dsn: str | None = None) -> None:
    """Idempotent runtime DDL — the twin of ``infra/initdb/14-campaign-examples.sql``."""
    with _connect(dsn) as conn:
        for stmt in _DDL.split(";"):
            if stmt.strip():
                conn.execute(stmt)


def example_id(tenant_id: str, campaign_name: str) -> str:
    """Deterministic, tenant-scoped example id — stable across re-ingests so pattern
    evidence and ju1.4 draft citations never dangle."""
    digest = hashlib.sha1(f"{tenant_id}|{campaign_name}".encode()).hexdigest()[:16]
    return f"cex_{digest}"


def pattern_id(tenant_id: str, pattern_key: str) -> str:
    digest = hashlib.sha1(f"{tenant_id}|{pattern_key}".encode()).hexdigest()[:16]
    return f"pat_{digest}"


# --------------------------------------------------------------------------- #
# Parse — pure, no DB. Missing key == not visible in the screenshot -> None.
# --------------------------------------------------------------------------- #
def parse_examples_json(content: str) -> dict[str, Any]:
    """Parse the transcription JSON into example records + an honest summary.

    Returns ``{"provenance", "examples", "summary"}``. Every example carries every
    known field (missing -> None, never inferred) plus the ``source`` badge. Unknown
    fields are reported per campaign in ``summary["unknown_fields"]``; an entry with
    no ``campaign_name`` cannot be keyed and is skipped WITH a reason. Pure."""
    doc = json.loads(content)
    provenance = doc.get("_provenance")
    examples: list[dict[str, Any]] = []
    unknown_fields: dict[str, list[str]] = {}
    skipped: list[dict[str, Any]] = []
    campaigns = doc.get("campaigns") or []
    for idx, entry in enumerate(campaigns):
        name = (entry.get("campaign_name") or "").strip()
        if not name:
            skipped.append({"index": idx, "reason": "no campaign_name"})
            continue
        unknown = sorted(set(entry) - set(_EXAMPLE_COLS))
        if unknown:
            unknown_fields[name] = unknown
        ex: dict[str, Any] = {col: entry.get(col) for col in _EXAMPLE_COLS}
        ex["campaign_name"] = name
        ex["source"] = SOURCE_OPERATOR_SCREENSHOT
        examples.append(ex)
    return {
        "provenance": provenance,
        "examples": examples,
        "summary": {
            "examples_seen": len(campaigns),
            "parsed": len(examples),
            "skipped": skipped,
            "unknown_fields": unknown_fields,
        },
    }


# --------------------------------------------------------------------------- #
# Pattern extraction — deterministic, keyless-safe (pure code, zero model calls).
# --------------------------------------------------------------------------- #
_SPECIAL_RE = re.compile(r"full[- ]?day|special|session", re.IGNORECASE)
_SCARCITY_RE = re.compile(
    r"limited\s+\d+\s+spots?|\bspots?\s+left\b|\bdown\s+to\s+\d+\b"
    r"|while\s+spots\s+are\s+still\s+available",
    re.IGNORECASE,
)
_REPLY_CTA_RE = re.compile(r"(?i:\b(?:reply|text))\s+([A-Z]{2,})\b")
_PAYMENT_RE = re.compile(r"klarna|affirm|payment\s+plans?", re.IGNORECASE)
_PERSONAL_RE = re.compile(r"personally\s+reach\s+out", re.IGNORECASE)
_STOP_RE = re.compile(r"\bstop\b", re.IGNORECASE)


def _text(ex: dict[str, Any], *fields: str) -> str:
    return "\n".join(str(ex.get(f) or "") for f in fields)


def _pct(part: Any, whole: Any) -> float | None:
    if not isinstance(part, (int, float)) or not isinstance(whole, (int, float)) or not whole:
        return None
    return round(100.0 * part / whole, 1)


def _rng(values: list[float]) -> dict[str, float]:
    return {"min": min(values), "max": max(values)}


def extract_patterns(examples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The deterministic pattern summary over ``examples`` (each must carry ``id``).

    Every pattern row links the example ids that EVIDENCE it; a detector with no
    evidence emits nothing (a pattern is never asserted without a real example behind
    it). Sorted by ``pattern_key`` — same input, byte-identical output."""
    patterns: list[dict[str, Any]] = []

    def emit(key: str, description: str, evidence: list[str],
             detail: dict[str, Any] | None = None) -> None:
        if evidence:
            patterns.append({
                "pattern_key": key, "description": description,
                "evidence_example_ids": evidence, "detail": detail,
            })

    emit(
        "artist_special",
        "artist-fronted special: a named artist opens the offer (full-day/special session)",
        [ex["id"] for ex in examples
         if ex.get("artist_name") and _SPECIAL_RE.search(_text(ex, "offer_type", "message_copy"))],
    )

    priced = [ex for ex in examples if ex.get("offer_price_usd") is not None]
    prices = sorted({ex["offer_price_usd"] for ex in priced})
    emit(
        "price_anchor",
        "price-anchored offer: the send names the session price up front "
        f"({', '.join(f'${p}' for p in prices)})",
        [ex["id"] for ex in priced],
        {"prices": prices},
    )

    emit(
        "limited_spots_scarcity",
        "limited-spots scarcity: a capped spot count, counted down in follow-ups",
        [ex["id"] for ex in examples if _SCARCITY_RE.search(_text(ex, "message_copy"))],
    )

    by_name = {ex["campaign_name"]: ex["id"] for ex in examples}
    participating: set[str] = set()
    pairs: list[dict[str, str]] = []
    unpaired: list[str] = []
    for ex in examples:
        if not ex.get("follow_up_to"):
            continue
        participating.add(ex["id"])
        opener = by_name.get(ex["follow_up_to"])
        if opener:
            pairs.append({"opener": opener, "follow_up": ex["id"]})
            participating.add(opener)
        else:
            unpaired.append(ex["id"])
    emit(
        "opener_followup_sequence",
        "opener -> scarcity follow-up sequence to the same audience days later",
        [ex["id"] for ex in examples if ex["id"] in participating],
        {"pairs": pairs, "unpaired_follow_ups": unpaired},
    )

    reply_cta: list[str] = []
    for ex in examples:
        artist = (ex.get("artist_name") or "").upper()
        tokens = {m.group(1) for m in _REPLY_CTA_RE.finditer(_text(ex, "cta", "message_copy"))}
        if artist and artist in tokens:
            reply_cta.append(ex["id"])
    emit(
        "reply_artist_cta",
        "reply-{ARTIST-NAME} keyword CTA (reply/text the artist's name to claim)",
        reply_cta,
    )

    emit(
        "payment_plan_angle",
        "payment-plan angle: Klarna/Affirm or 'payment plan options' to split the price",
        [ex["id"] for ex in examples
         if ex.get("payment_plans") or _PAYMENT_RE.search(_text(ex, "message_copy"))],
    )

    emit(
        "personal_outreach_framing",
        "personal-outreach framing ('X wanted me to personally reach out')",
        [ex["id"] for ex in examples if _PERSONAL_RE.search(_text(ex, "message_copy"))],
    )

    emit(
        "artwork_attachment_on_opener",
        "artwork image attached on the opener (follow-ups go without)",
        [ex["id"] for ex in examples
         if ex.get("attachment_present") is True and not ex.get("follow_up_to")],
    )

    emit(
        "stop_opt_out",
        "every send carries an explicit reply-STOP opt-out line",
        [ex["id"] for ex in examples if _STOP_RE.search(_text(ex, "opt_out_text", "message_copy"))],
    )

    emit(
        "category_location_targeting",
        "targeted sends: category tags, per-artist selection, or a per-location audience",
        [ex["id"] for ex in examples
         if ex.get("categories") or ex.get("artists_selected") or ex.get("location")],
    )

    per_example: list[dict[str, Any]] = []
    for ex in examples:
        delivered = _pct(ex.get("delivered_count"), ex.get("recipient_count"))
        if delivered is None:
            continue
        per_example.append({
            "example_id": ex["id"],
            "delivered_pct": delivered,
            "failed_pct": _pct(ex.get("failed_count"), ex.get("recipient_count")),
            "dnd_blocked_pct": _pct(ex.get("dnd_blocked_count"), ex.get("recipient_count")),
        })
    if per_example:
        delivered_rng = _rng([p["delivered_pct"] for p in per_example])
        detail: dict[str, Any] = {"delivered_pct": delivered_rng, "per_example": per_example}
        for k in ("failed_pct", "dnd_blocked_pct"):
            vals = [p[k] for p in per_example if p[k] is not None]
            if vals:
                detail[k] = _rng(vals)
        emit(
            "delivery_reality",
            "delivery reality (computed from the real counts): "
            f"{delivered_rng['min']:g}-{delivered_rng['max']:g}% delivered"
            + (f", up to {detail['dnd_blocked_pct']['max']:g}% DND-blocked"
               if "dnd_blocked_pct" in detail else "")
            + " — DND compliance and list hygiene are first-class in this client's world",
            [p["example_id"] for p in per_example],
            detail,
        )

    return sorted(patterns, key=lambda p: p["pattern_key"])


# --------------------------------------------------------------------------- #
# Ingest — idempotent upsert + pattern refresh.
# --------------------------------------------------------------------------- #
def import_campaign_examples(
    json_path: Path | str, tenant_id: str = "skindesign", *, dsn: str | None = None
) -> dict[str, Any]:
    """Parse + persist the campaign-examples JSON for ``tenant_id`` (idempotent upsert
    by deterministic id) and refresh the tenant's pattern rows from ALL its examples.
    Returns the parse summary + created/updated/pattern counts."""
    text = Path(json_path).read_text(encoding="utf-8")
    parsed = parse_examples_json(text)
    provenance = json.dumps(parsed["provenance"]) if parsed["provenance"] else None
    ensure_schema(dsn)

    cols = ", ".join(_EXAMPLE_COLS)
    placeholders = ", ".join(
        "%s::jsonb" if c in _JSONB_COLS else "%s" for c in _EXAMPLE_COLS
    )
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in _EXAMPLE_COLS)
    created = updated = 0
    with _connect(dsn) as conn:
        existing = {
            r["id"] for r in conn.execute(
                "SELECT id FROM campaign_examples WHERE tenant_id = %s", (tenant_id,)
            )
        }
        for ex in parsed["examples"]:
            ex["id"] = example_id(tenant_id, ex["campaign_name"])
            values = [
                json.dumps(ex[c]) if c in _JSONB_COLS and ex[c] is not None else ex[c]
                for c in _EXAMPLE_COLS
            ]
            conn.execute(
                f"INSERT INTO campaign_examples (id, tenant_id, source, provenance, {cols}) "
                f"VALUES (%s, %s, %s, %s::jsonb, {placeholders}) "
                f"ON CONFLICT (id) DO UPDATE SET {updates}, "
                "source = EXCLUDED.source, provenance = EXCLUDED.provenance, "
                "updated_at = now()",
                [ex["id"], tenant_id, SOURCE_OPERATOR_SCREENSHOT, provenance, *values],
            )
            if ex["id"] in existing:
                updated += 1
            else:
                created += 1

        patterns = extract_patterns(parsed["examples"])
        for p in patterns:
            conn.execute(
                "INSERT INTO campaign_example_patterns "
                "(id, tenant_id, pattern_key, description, evidence_example_ids, detail) "
                "VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb) "
                "ON CONFLICT (id) DO UPDATE SET description = EXCLUDED.description, "
                "evidence_example_ids = EXCLUDED.evidence_example_ids, "
                "detail = EXCLUDED.detail, updated_at = now()",
                [
                    pattern_id(tenant_id, p["pattern_key"]), tenant_id, p["pattern_key"],
                    p["description"], json.dumps(p["evidence_example_ids"]),
                    json.dumps(p["detail"]) if p["detail"] is not None else None,
                ],
            )
        # A pattern whose evidence vanished must not linger (refresh semantics).
        keys = [p["pattern_key"] for p in patterns]
        conn.execute(
            "DELETE FROM campaign_example_patterns "
            "WHERE tenant_id = %s AND NOT (pattern_key = ANY(%s))",
            (tenant_id, keys),
        )

    summary = dict(parsed["summary"])
    summary.update({
        "tenant_id": tenant_id, "created": created, "updated": updated,
        "patterns": len(patterns),
    })
    return summary


# --------------------------------------------------------------------------- #
# Retrieval API — for the generator (ju1.4) + supervisor. Honest-empty.
# --------------------------------------------------------------------------- #
def get_examples(
    tenant_id: str, artist: str | None = None, *, dsn: str | None = None
) -> list[dict[str, Any]]:
    """The tenant's real campaign examples, optionally filtered by artist name
    (case-insensitive). An artist/tenant with no examples reads as ``[]`` — never a
    fabricated example. Best-effort: a store hiccup yields ``[]``."""
    sql = "SELECT * FROM campaign_examples WHERE tenant_id = %s"
    params: list[Any] = [tenant_id]
    if artist is not None:
        sql += " AND lower(artist_name) = lower(%s)"
        params.append(artist)
    sql += " ORDER BY sent_at NULLS LAST, campaign_name"
    try:
        with _connect(dsn) as conn:
            return [dict(r) for r in conn.execute(sql, params)]
    except Exception:
        return []


def get_patterns(tenant_id: str, *, dsn: str | None = None) -> list[dict[str, Any]]:
    """The tenant's extracted pattern rows, each linking its evidence example ids.
    ``[]`` when none — a pattern is never fabricated. Best-effort like the reads
    elsewhere in the studio stores."""
    try:
        with _connect(dsn) as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM campaign_example_patterns WHERE tenant_id = %s "
                "ORDER BY pattern_key",
                (tenant_id,),
            )]
    except Exception:
        return []


def main(argv: list[str] | None = None) -> int:
    """CLI: ingest the transcription JSON for a tenant and print the honest summary."""
    import sys

    args = argv if argv is not None else sys.argv[1:]
    path = Path(args[0]) if args else Path(
        "C:/Users/Links/Desktop/CustomerAcq/client-data/campaign-examples.json"
    )
    tenant = args[1] if len(args) > 1 else "skindesign"
    print(json.dumps(import_campaign_examples(path, tenant), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
