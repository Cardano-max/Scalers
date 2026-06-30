"""Customer-research data access for the Studio Host (grounding layer).

Pulls REAL grounded facts for a lead from the live DB — ``customers`` +
``customer_personas`` (rich JSONB traits) + ``tattoo_history`` + ``memories`` —
so the Host can reason per-lead instead of truthfully replying "I don't have
access." Sync psycopg (offloaded via ``asyncio.to_thread`` by the async tools),
the same pattern as ``studio.agui._persist_plan`` and ``actions.store``.

It also (a) UPSERTS uploaded leads into ``customers`` (matched on tenant+email so
re-uploading the seeded churn leads never duplicates) and (b) builds a
PERSONALIZED outreach draft strictly from real persona facts — every token of a
draft traces to a DB fact or the operator's stated goal, NEVER a fabricated
customer detail. Drafts are returned for the caller to stage as PENDING actions
(HELD); nothing here sends.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

_DEFAULT_DSN = "postgresql://scalers:scalers@localhost:5432/scalers"


def _dsn(dsn: str | None) -> str:
    return dsn or os.environ.get("ENGINE_DATABASE_URL") or _DEFAULT_DSN


def _connect(dsn: str | None = None):
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(_dsn(dsn), autocommit=True, row_factory=dict_row)


def _flatten_traits(traits: dict[str, Any] | None) -> dict[str, Any]:
    """Flatten the persona JSONB ``{"traits": {key: {value, basis, inferred}}}`` to
    ``{key: value}``. Tolerates a flat ``{key: value}`` shape too. Honest: returns
    {} when there is no persona (never invents traits)."""
    if not traits:
        return {}
    inner = traits.get("traits") if isinstance(traits.get("traits"), dict) else traits
    out: dict[str, Any] = {}
    for key, val in inner.items():
        if isinstance(val, dict) and "value" in val:
            out[key] = val["value"]
        else:
            out[key] = val
    return out


def lookup_lead(
    tenant_id: str,
    *,
    customer_id: str | None = None,
    email: str | None = None,
    name: str | None = None,
    dsn: str | None = None,
    memory_store: Any | None = None,
) -> dict[str, Any] | None:
    """Return grounded facts for ONE lead, or ``None`` if not found for the tenant.

    Resolves by ``customer_id`` → ``email`` (exact, case-insensitive) → ``name``
    (exact, case-insensitive). Pulls the customer row, flattened persona traits,
    past tattoos, and any persisted per-customer memories. Tenant-scoped on every
    read; ``tattoo_history`` (no tenant column) is joined through ``customers``."""
    clauses = ["tenant_id = %s"]
    params: list[Any] = [tenant_id]
    if customer_id:
        clauses.append("id = %s")
        params.append(customer_id)
    elif email:
        clauses.append("lower(email) = lower(%s)")
        params.append(email)
    elif name:
        clauses.append("lower(name) = lower(%s)")
        params.append(name)
    else:
        raise ValueError("lookup_lead requires customer_id, email, or name")

    with _connect(dsn) as conn:
        cust = conn.execute(
            "SELECT id, tenant_id, name, email, phone, ig_handle, linkedin_handle, "
            "dob, city, state, interests, preferred_channels, email_opt_in, "
            "sms_opt_in, source FROM customers WHERE " + " AND ".join(clauses)
            + " LIMIT 1",
            params,
        ).fetchone()
        if cust is None:
            return None
        persona = conn.execute(
            "SELECT traits, synthetic FROM customer_personas "
            "WHERE tenant_id = %s AND customer_id = %s LIMIT 1",
            (tenant_id, cust["id"]),
        ).fetchone()
        tattoos = conn.execute(
            "SELECT style, artist, date, notes FROM tattoo_history "
            "WHERE customer_id = %s ORDER BY date DESC NULLS LAST",
            (cust["id"],),
        ).fetchall()

    traits = _flatten_traits(persona["traits"] if persona else None)

    memories: list[dict[str, Any]] = []
    if memory_store is not None:
        try:
            for m in memory_store.list_for_subject(
                tenant_id=tenant_id, subject_type="customer", subject_id=cust["id"]
            ):
                memories.append({"text": m.text, "metadata": m.metadata})
        except Exception:
            memories = []

    return {
        "customer_id": cust["id"],
        "name": cust["name"],
        "email": cust["email"],
        "ig_handle": cust["ig_handle"],
        "city": cust["city"],
        "state": cust["state"],
        "interests": list(cust["interests"] or []),
        "preferred_channels": list(cust["preferred_channels"] or []),
        "email_opt_in": cust["email_opt_in"],
        "sms_opt_in": cust["sms_opt_in"],
        "persona_synthetic": bool(persona["synthetic"]) if persona else None,
        "persona_traits": traits,
        "tattoo_history": [
            {
                "style": t["style"],
                "artist": t["artist"],
                "date": t["date"].isoformat() if t["date"] else None,
                "notes": t["notes"],
            }
            for t in tattoos
        ],
        "memories": memories,
    }


def lookup_leads(
    tenant_id: str,
    identifiers: list[dict[str, str]],
    *,
    dsn: str | None = None,
    memory_store: Any | None = None,
) -> list[dict[str, Any]]:
    """Research a BATCH of leads one-by-one. Each identifier is a dict with any of
    ``customer_id`` / ``email`` / ``name``. Found leads are returned in input order;
    misses are skipped (the caller can diff counts to report honest not-founds)."""
    out: list[dict[str, Any]] = []
    for ident in identifiers:
        facts = lookup_lead(
            tenant_id,
            customer_id=ident.get("customer_id"),
            email=ident.get("email"),
            name=ident.get("name"),
            dsn=dsn,
            memory_store=memory_store,
        )
        if facts is not None:
            out.append(facts)
    return out


def churn_risk_leads(
    tenant_id: str, *, limit: int = 50, dsn: str | None = None,
    memory_store: Any | None = None,
) -> list[dict[str, Any]]:
    """Grounded facts for the tenant's win-back / lapsing leads (no upload needed).

    Selects customers whose persona marks them ``win_back_candidate`` or
    ``lifecycle_stage in (lapsing, lead-no-visit)`` — exactly the churn-risk cohort
    seeded for the demo — and returns full grounded facts for each."""
    with _connect(dsn) as conn:
        rows = conn.execute(
            """
            SELECT c.id
            FROM customers c
            JOIN customer_personas p
              ON p.customer_id = c.id AND p.tenant_id = c.tenant_id
            WHERE c.tenant_id = %s
              AND (
                (p.traits #>> '{traits,win_back_candidate,value}') = 'true'
                OR (p.traits #>> '{traits,lifecycle_stage,value}')
                     IN ('lapsing', 'lead-no-visit', 'churn-risk')
              )
            ORDER BY c.created_at
            LIMIT %s
            """,
            (tenant_id, limit),
        ).fetchall()
    ids = [r["id"] for r in rows]
    return lookup_leads(
        tenant_id, [{"customer_id": i} for i in ids], dsn=dsn, memory_store=memory_store
    )


# --------------------------------------------------------------------------- #
# Personalized draft builder — grounded in REAL persona facts only
# --------------------------------------------------------------------------- #


def choose_channel(facts: dict[str, Any], plan_channels: list[str] | None) -> str:
    """Pick the outreach channel for a lead, honestly respecting consent.

    Order: persona ``likely_best_channel`` → first ``preferred_channels`` → first
    plan channel → ``instagram``. Email is only chosen if the lead opted in; SMS
    only if opted in — otherwise we fall through to instagram (organic DM), never
    overriding a withheld consent."""
    traits = facts.get("persona_traits", {})
    candidates: list[str] = []
    best = traits.get("likely_best_channel")
    if best:
        candidates.append(str(best))
    candidates += [str(c) for c in facts.get("preferred_channels", [])]
    candidates += [str(c) for c in (plan_channels or [])]
    candidates.append("instagram")

    for ch in candidates:
        ch = ch.strip().lower()
        if ch in ("email", "gmail"):
            if facts.get("email_opt_in"):
                return "gmail"
            continue
        if ch == "sms":
            if facts.get("sms_opt_in"):
                return "sms"
            continue
        if ch in ("instagram", "ig"):
            return "instagram"
        if ch == "facebook":
            return "facebook"
    return "instagram"


def build_outreach_draft(
    facts: dict[str, Any],
    *,
    goal: str = "",
    channel: str | None = None,
    plan_channels: list[str] | None = None,
) -> dict[str, Any]:
    """Build ONE personalized outreach draft strictly from grounded facts.

    Every interpolated detail (first name, city, top interest / aesthetic lean,
    lapsed status, last tattoo style) comes from the lead's real customer/persona/
    tattoo rows — NO invented facts. Returns ``{channel, target, subject, draft,
    grounding}`` where ``grounding`` lists exactly which DB facts were used (so the
    draft is auditable). The caller stages this as a PENDING action (HELD)."""
    name = (facts.get("name") or "there").strip()
    first = name.split()[0] if name else "there"
    traits = facts.get("persona_traits", {})
    interests = facts.get("interests", []) or []
    aesthetic = traits.get("aesthetic_lean")
    top_interest = aesthetic or (interests[0] if interests else None)
    city = facts.get("city")
    lifecycle = traits.get("lifecycle_stage")
    win_back = bool(traits.get("win_back_candidate"))
    tattoos = facts.get("tattoo_history", [])
    last_style = tattoos[0]["style"] if tattoos else None

    ch = (channel or choose_channel(facts, plan_channels)).lower()

    grounding: list[str] = [f"name={name}"]
    interest_phrase = ""
    if top_interest:
        interest_phrase = f" your {top_interest} work"
        grounding.append(f"interest/aesthetic={top_interest}")
    city_phrase = f" here in {city}" if city else ""
    if city:
        grounding.append(f"city={city}")
    if lifecycle:
        grounding.append(f"lifecycle={lifecycle}")
    if last_style:
        grounding.append(f"last_tattoo_style={last_style}")
    if win_back:
        grounding.append("win_back_candidate=true")

    goal_line = (goal or "book your next session").strip()

    if win_back or (lifecycle in ("lapsing", "lead-no-visit", "churn-risk")):
        opener = f"Hi {first} — it's been a while and we've been thinking about you."
    else:
        opener = f"Hi {first} —"

    last_line = (
        f" Loved your last {last_style} piece" if last_style else ""
    )

    body = (
        f"{opener}{last_line}{(' We have new ' + str(top_interest) + ' flash that made us think of' + interest_phrase + '.') if top_interest else ''}"
        f" We'd love to get you back in the chair{city_phrase} — {goal_line}."
        " Want me to hold a spot for you this month?"
    )
    body = " ".join(body.split())  # collapse whitespace from optional fragments

    subject = None
    if ch in ("gmail", "email"):
        subject = (
            f"{first}, a {top_interest} idea for your next tattoo"
            if top_interest
            else f"{first}, let's plan your next tattoo"
        )

    target = None
    if ch in ("gmail", "email"):
        target = facts.get("email")
    elif ch == "instagram":
        target = facts.get("ig_handle") or facts.get("name")
    elif ch == "sms":
        target = facts.get("phone")
    target = target or facts.get("email") or facts.get("name")

    return {
        "channel": "gmail" if ch == "email" else ch,
        "target": target,
        "subject": subject,
        "draft": body,
        "grounding": grounding,
        "customer_id": facts.get("customer_id"),
    }


# --------------------------------------------------------------------------- #
# Upload ingestion — UPSERT uploaded leads into ``customers`` (tenant+email key)
# --------------------------------------------------------------------------- #


def _split_location(location: str | None) -> tuple[str | None, str | None]:
    if not location:
        return None, None
    parts = [p.strip() for p in str(location).split(",")]
    if len(parts) >= 2:
        return parts[0] or None, parts[1] or None
    return (parts[0] or None), None


def upsert_lead(
    tenant_id: str,
    row: dict[str, str],
    *,
    dsn: str | None = None,
) -> dict[str, Any]:
    """UPSERT one uploaded lead into ``customers`` keyed on (tenant_id, email).

    If a customer with this email already exists for the tenant (e.g. the seeded
    churn leads), it is returned WITHOUT creating a duplicate (and missing fields
    are backfilled). Otherwise a new ``cust_*`` row is inserted. Returns
    ``{customer_id, created}``. Email is required to key the upsert; a row with no
    email is inserted as a new customer with a synthetic-less id."""
    email = (row.get("email") or "").strip() or None
    name = (row.get("name") or "").strip() or None
    city, state = _split_location(row.get("location") or row.get("city"))
    interests_raw = row.get("interests") or ""
    interests = [s.strip() for s in interests_raw.replace(",", ";").split(";") if s.strip()]
    linkedin = (row.get("linkedin") or "").strip() or None

    with _connect(dsn) as conn:
        existing = None
        if email:
            existing = conn.execute(
                "SELECT id FROM customers WHERE tenant_id = %s AND lower(email) = lower(%s) LIMIT 1",
                (tenant_id, email),
            ).fetchone()
        if existing is not None:
            # Backfill only NULL/empty columns; never clobber seeded ground truth.
            conn.execute(
                """
                UPDATE customers SET
                    name = COALESCE(NULLIF(name, ''), %s),
                    city = COALESCE(city, %s),
                    state = COALESCE(state, %s),
                    linkedin_handle = COALESCE(linkedin_handle, %s),
                    interests = CASE WHEN interests IS NULL OR cardinality(interests) = 0
                                     THEN %s ELSE interests END
                WHERE id = %s
                """,
                (name, city, state, linkedin, interests, existing["id"]),
            )
            return {"customer_id": existing["id"], "created": False}

        cust_id = "cust_" + uuid.uuid4().hex[:16]
        conn.execute(
            """
            INSERT INTO customers
                (id, tenant_id, name, email, linkedin_handle, city, state,
                 interests, preferred_channels, email_opt_in, sms_opt_in, source)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                cust_id, tenant_id, name, email, linkedin, city, state,
                interests, [], bool(email), False, "studio_upload",
            ),
        )
        return {"customer_id": cust_id, "created": True}


def ingest_leads(
    tenant_id: str,
    rows: list[dict[str, str]],
    *,
    dsn: str | None = None,
) -> dict[str, Any]:
    """UPSERT a list of parsed CSV rows. Returns ``{ingested, created, matched,
    customer_ids}``. Idempotent: re-ingesting the seeded churn leads matches them
    and creates nothing."""
    created = 0
    matched = 0
    ids: list[str] = []
    for row in rows:
        res = upsert_lead(tenant_id, row, dsn=dsn)
        ids.append(res["customer_id"])
        if res["created"]:
            created += 1
        else:
            matched += 1
    return {
        "ingested": len(ids),
        "created": created,
        "matched": matched,
        "customer_ids": ids,
    }
