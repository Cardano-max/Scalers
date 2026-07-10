"""Campaign Intelligence — the executive brain (read-only, evidence-only).

One endpoint answers "what should we run next, and WHY" from the five
memories the system already keeps: campaign memory (real past campaigns +
extracted patterns), artist memory (portfolio + proven voice), customer
memory (objections read from real conversations), competitor memory (when
present), and the review-queue/send state. Every recommendation carries its
evidence — counts and ids from real rows, never a vibe. Deterministic (zero
tokens): the intelligence here is aggregation + explicit rules; the LLM adds
nothing a wrong number wouldn't poison.
"""

from __future__ import annotations

import os
from typing import Any


def _dsn(dsn: str | None) -> str:
    return dsn or os.environ.get(
        "ENGINE_DATABASE_URL", "postgresql://scalers:scalers@localhost:5432/scalers"
    )


def _connect(dsn: str | None):
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(_dsn(dsn), row_factory=dict_row, autocommit=True)


def _q(conn, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    try:
        return conn.execute(sql, params).fetchall()
    except Exception:
        return []


def campaign_intelligence(tenant_id: str, *, dsn: str | None = None) -> dict[str, Any]:
    """The full read. Sections are independent and honest-empty when a store
    has nothing — a missing table never fails the whole board."""
    with _connect(dsn) as conn:
        # Best past campaigns by REAL delivery performance.
        campaigns = _q(conn, """
            SELECT campaign_name, artist_name, offer_price_usd, offer_type, cta,
                   recipient_count, delivered_count,
                   CASE WHEN recipient_count > 0
                        THEN round(delivered_count::numeric / recipient_count, 3)
                   END AS delivery_rate
              FROM campaign_examples
             WHERE tenant_id = %s AND recipient_count IS NOT NULL
             ORDER BY delivered_count DESC NULLS LAST LIMIT 5
        """, (tenant_id,))

        patterns = _q(conn, """
            SELECT pattern_key, description,
                   coalesce(jsonb_array_length(to_jsonb(evidence_example_ids)), 0) AS evidence_n
              FROM campaign_example_patterns
             ORDER BY 3 DESC LIMIT 8
        """)

        # Artist portfolio depth (real library counts; performance joins later).
        artists = _q(conn, """
            SELECT content->>'artist' AS artist,
                   count(*) AS pieces,
                   count(*) FILTER (WHERE content->>'media' = 'video') AS videos
              FROM assets
             WHERE coalesce(content->>'artist','') <> ''
             GROUP BY 1 ORDER BY pieces DESC LIMIT 8
        """)

        # Objection landscape from the analysts' REAL per-lead reads.
        objections = _q(conn, """
            SELECT output->>'primary_objection' AS objection, count(*) AS leads
              FROM agent_runs
             WHERE role = 'analyst' AND output->>'primary_objection' IS NOT NULL
               AND output->>'primary_objection' NOT IN ('', 'none-found')
             GROUP BY 1 ORDER BY leads DESC LIMIT 8
        """)

        queue = _q(conn, """
            SELECT channel, count(*) AS pending
              FROM actions WHERE tenant_id = %s AND status = 'pending'
             GROUP BY channel
        """, (tenant_id,))

        competitors = _q(conn, """
            SELECT handle, url, total_score, why_it_worked
              FROM competitor_posts
             WHERE tenant_id = %s AND total_score IS NOT NULL
             ORDER BY total_score DESC LIMIT 5
        """, (tenant_id,))

        conversations = _q(conn, """
            SELECT count(*) AS n FROM lead_conversations WHERE tenant_id = %s
        """, (tenant_id,))

    # ── Recommendations: explicit rules, each carrying its evidence. ────────── #
    recs: list[dict[str, Any]] = []
    best = campaigns[0] if campaigns else None
    if best and best.get("cta"):
        recs.append({
            "recommend": (
                f"Reuse the proven CTA pattern from {best['campaign_name']!r} "
                f"(\"{best['cta']}\") for the next {best.get('artist_name') or 'artist'} push"
            ),
            "why": (
                f"it delivered {best.get('delivered_count')} of "
                f"{best.get('recipient_count')} recipients "
                f"(rate {best.get('delivery_rate')})"
            ),
            "evidence": {"campaign": best["campaign_name"]},
        })
    price_objs = next((o for o in objections if "price" in (o["objection"] or "")), None)
    if price_objs:
        recs.append({
            "recommend": "Run a payment-plan-forward winback for the price-objection segment",
            "why": (
                f"{price_objs['leads']} analyzed lead(s) show a price objection; the "
                "studio's Klarna/Affirm framing is a substantiated, proven answer"
            ),
            "evidence": {"objection_counts": objections},
        })
    if conversations and int(conversations[0]["n"]) > 0:
        recs.append({
            "recommend": (
                "Reactivation pass over the imported conversation cohort — one strategy "
                "per lead from their REAL thread"
            ),
            "why": f"{conversations[0]['n']} verbatim conversation(s) on file carry real objections",
            "evidence": {"lead_conversations": int(conversations[0]["n"])},
        })
    if competitors:
        recs.append({
            "recommend": (
                f"Mold the top competitor pattern ({competitors[0]['handle']}, score "
                f"{competitors[0]['total_score']}) onto our artwork for the next IG post"
            ),
            "why": competitors[0].get("why_it_worked") or "highest scored pattern on file",
            "evidence": {"url": competitors[0].get("url")},
        })
    if not recs:
        recs.append({
            "recommend": "Import campaign history / conversations to unlock recommendations",
            "why": "no performance, objection, or competitor evidence on file yet",
            "evidence": {},
        })

    return _jsonable({
        "tenantId": tenant_id,
        "bestCampaigns": campaigns,
        "patterns": patterns,
        "artists": artists,
        "objections": objections,
        "reviewQueue": queue,
        "competitors": competitors,
        "recommendations": recs,
    })


def _jsonable(value: Any) -> Any:
    """Recursively coerce DB scalar types (Decimal, datetime) to JSON-safe ones."""
    import datetime as _dt
    from decimal import Decimal

    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (_dt.datetime, _dt.date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value
