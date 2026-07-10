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

        # TENANT SCOPING on every section (a QA empty-tenant probe caught this
        # board presenting the incumbent client's patterns/artists/objections
        # under a brand-new tenant's id — a fabrication AND a confidentiality
        # leak). patterns: direct tenant column; artists: the assets library is
        # bucketed by campaign_id='portfolio:<tenant>'; objections: agent_runs
        # carries no tenant column, so attribute through its run's row.
        patterns = _q(conn, """
            SELECT pattern_key, description,
                   coalesce(jsonb_array_length(to_jsonb(evidence_example_ids)), 0) AS evidence_n
              FROM campaign_example_patterns
             WHERE tenant_id = %s
             ORDER BY 3 DESC LIMIT 8
        """, (tenant_id,))

        # Artist portfolio depth (real library counts; performance joins later).
        artists = _q(conn, """
            SELECT content->>'artist' AS artist,
                   count(*) AS pieces,
                   count(*) FILTER (WHERE content->>'media' = 'video') AS videos
              FROM assets
             WHERE coalesce(content->>'artist','') <> ''
               AND campaign_id = %s
             GROUP BY 1 ORDER BY pieces DESC LIMIT 8
        """, (f"portfolio:{tenant_id}",))

        # Objection landscape from the analysts' REAL per-lead reads.
        objections = _q(conn, """
            SELECT ar.output->>'primary_objection' AS objection, count(*) AS leads
              FROM agent_runs ar
              JOIN runs r ON r.run_id = ar.run_id AND r.tenant_id = %s
             WHERE ar.role = 'analyst' AND ar.output->>'primary_objection' IS NOT NULL
               AND ar.output->>'primary_objection' NOT IN ('', 'none-found')
             GROUP BY 1 ORDER BY leads DESC LIMIT 8
        """, (tenant_id,))

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

        # How many of the conversation leads ALREADY carry an analyst-classified
        # objection — the real per-lead linkage: the analyst step's input carries the
        # customer_id, its run's row carries the tenant, and lead_conversations keys
        # the same (tenant_id, customer_id). This keeps the recommendation's wording
        # matched to the evidence: conversations ON FILE is not the same claim as
        # objections CLASSIFIED.
        conv_classified = _q(conn, """
            SELECT count(DISTINCT ar.input->>'customer_id') AS n
              FROM agent_runs ar
              JOIN runs r ON r.run_id = ar.run_id AND r.tenant_id = %s
             WHERE ar.role = 'analyst'
               AND ar.output->>'primary_objection' IS NOT NULL
               AND ar.output->>'primary_objection' NOT IN ('', 'none-found')
               AND ar.input->>'customer_id' IN (
                     SELECT customer_id FROM lead_conversations WHERE tenant_id = %s)
        """, (tenant_id, tenant_id))

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
        # WORDING MATCHES THE EVIDENCE (truth-gap fix): the count is of conversations
        # on file — whether they "carry real objections" is only known once the
        # analyst has classified each lead, so say exactly which of the two we know.
        n_conv = int(conversations[0]["n"])
        n_classified = int(conv_classified[0]["n"]) if conv_classified else 0
        if n_classified > 0:
            why = (
                f"{n_conv} verbatim conversation(s) on file; {n_classified} of those "
                "leads already carry an analyst-classified objection"
            )
        else:
            why = (
                f"{n_conv} verbatim conversation(s) on file — run the reactivation "
                "pass to classify each"
            )
        recs.append({
            "recommend": (
                "Reactivation pass over the imported conversation cohort — one strategy "
                "per lead from their REAL thread"
            ),
            "why": why,
            "evidence": {
                "lead_conversations": n_conv,
                "leads_with_classified_objection": n_classified,
            },
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
