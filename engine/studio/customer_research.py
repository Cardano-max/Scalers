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
import re
import uuid
from typing import Any

_DEFAULT_DSN = "postgresql://scalers:scalers@localhost:5432/scalers"


def _dsn(dsn: str | None) -> str:
    return dsn or os.environ.get("ENGINE_DATABASE_URL") or _DEFAULT_DSN


def _connect(dsn: str | None = None):
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(_dsn(dsn), autocommit=True, row_factory=dict_row)


# The extended, adapter-normalized tattoo-lead columns (ADR §4.6). Added to the
# infra-provisioned ``customers`` table as nullable columns so the extended shape is
# reproducible without owning that table's DDL, and so the long-standing ``notes`` bug
# (read in five places, never populated) is fixed at the source. ``ADD COLUMN IF NOT
# EXISTS`` is idempotent + a no-op after the first run.
_LEAD_EXT_COLUMNS: tuple[str, ...] = (
    "notes", "artist", "shop", "lead_stage", "customer_type", "payment_status",
)
_columns_ensured: set[str] = set()


def ensure_lead_columns(dsn: str | None = None) -> None:
    """Idempotently add the extended tattoo-lead columns to ``customers`` (best-effort).

    Memoized per-DSN per-process so a batch of lookups runs the ALTER at most once. A
    fresh DB with no ``customers`` table (tests mock this away entirely) fails silently —
    the read/write then simply proceeds without the extended columns."""
    key = _dsn(dsn)
    if key in _columns_ensured:
        return
    try:
        with _connect(dsn) as conn:
            for col in _LEAD_EXT_COLUMNS:
                conn.execute(f"ALTER TABLE customers ADD COLUMN IF NOT EXISTS {col} TEXT")
        _columns_ensured.add(key)
    except Exception:
        # Do not cache on failure (the table may appear later); reads/writes degrade.
        pass


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

    ensure_lead_columns(dsn)
    with _connect(dsn) as conn:
        cust = conn.execute(
            "SELECT id, tenant_id, name, email, phone, ig_handle, linkedin_handle, "
            "dob, city, state, interests, preferred_channels, email_opt_in, "
            "sms_opt_in, source, notes, artist, shop, lead_stage, customer_type, "
            "payment_status FROM customers WHERE " + " AND ".join(clauses)
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
        # ``phone`` is already SELECTed above; surface it (honest-empty when null) so the
        # SMS channel targets a real number and the per-lead Dossier carries real contact.
        "phone": cust["phone"],
        "ig_handle": cust["ig_handle"],
        # Customer-provided LinkedIn handle (already SELECTed): surfaced so the
        # consent-safe social lookup (gather_social_context) can see it — only
        # handles the customer provided are ever looked up, never name discovery.
        "linkedin_handle": cust["linkedin_handle"],
        "city": cust["city"],
        "state": cust["state"],
        "interests": list(cust["interests"] or []),
        "preferred_channels": list(cust["preferred_channels"] or []),
        "email_opt_in": cust["email_opt_in"],
        "sms_opt_in": cust["sms_opt_in"],
        "persona_synthetic": bool(persona["synthetic"]) if persona else None,
        "persona_traits": traits,
        # Extended, adapter-normalized tattoo-lead fields (ADR §4.6). Honest-empty when
        # absent; ``notes`` here fixes the long-standing dead ``csv-note`` angle.
        "notes": cust.get("notes"),
        "artist": cust.get("artist"),
        "shop": cust.get("shop"),
        "lead_stage": cust.get("lead_stage"),
        "customer_type": cust.get("customer_type"),
        "payment_status": cust.get("payment_status"),
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
            -- Deterministic order: c.created_at collides for batch/seed inserts
            -- (one txn now()), so a bare created_at makes the LIMIT-N slice an
            -- ARBITRARY subset that Postgres may return differently across runs —
            -- a retry then picks DIFFERENT customers (nmh.11 vanish/re-stage). The
            -- unique c.id tiebreaker makes the cohort STABLE across retries.
            ORDER BY c.created_at, c.id
            LIMIT %s
            """,
            (tenant_id, limit),
        ).fetchall()
    ids = [r["id"] for r in rows]
    return lookup_leads(
        tenant_id, [{"customer_id": i} for i in ids], dsn=dsn, memory_store=memory_store
    )


def contactable_leads(
    tenant_id: str, *, limit: int = 50, exclude_ids: list[str] | None = None,
    dsn: str | None = None, memory_store: Any | None = None,
) -> list[dict[str, Any]]:
    """Grounded facts for the tenant's CONTACTABLE customers — any customer with a real
    contact method (email / phone / IG handle / name), excluding ``exclude_ids``.

    This is the general fill source behind the exact-draft-count requirement (spec §14,
    nmh.1): when the WARM + churn cohorts are short of the requested N, the run tops up
    from here so a request for 10 yields 10 drafts whenever the tenant has >=10 valid
    contacts. Ordered by ``created_at`` for a stable, reproducible cohort. Returns full
    grounded facts (same shape as the other lead sources)."""
    exclude = list(exclude_ids or [])
    with _connect(dsn) as conn:
        rows = conn.execute(
            """
            SELECT id FROM customers
            WHERE tenant_id = %s
              AND (email IS NOT NULL OR phone IS NOT NULL
                   OR ig_handle IS NOT NULL OR name IS NOT NULL)
              AND id <> ALL(%s)
            ORDER BY created_at, id
            LIMIT %s
            """,
            (tenant_id, exclude, limit),
        ).fetchall()
    ids = [r["id"] for r in rows]
    return lookup_leads(
        tenant_id, [{"customer_id": i} for i in ids], dsn=dsn, memory_store=memory_store
    )


def conversation_leads(
    tenant_id: str, *, limit: int = 50, dsn: str | None = None,
    memory_store: Any | None = None,
) -> list[dict[str, Any]]:
    """Grounded facts for the tenant's WARM leads — the customers that HAVE prior
    conversation history (a row in ``lead_conversations``).

    These are the operator's own warm leads and the whole point of the provided-leads
    pivot: per-lead psychology analysis off their REAL chat (e.g. Sarah Kim's price-
    objection SMS). Ordered by earliest conversation so the seeded cohort is stable.
    Returns full grounded facts for each; empty when the tenant has no conversation leads
    (the caller then falls back to the churn cohort). Best-effort — a store hiccup yields
    ``[]`` rather than a crash."""
    try:
        with _connect(dsn) as conn:
            rows = conn.execute(
                """
                SELECT customer_id, MIN(created_at) AS mc
                FROM lead_conversations
                WHERE tenant_id = %s
                GROUP BY customer_id
                -- Unique customer_id tiebreaker: MIN(created_at) collides freely
                -- across customers, so a bare ORDER BY mc makes the LIMIT-N slice
                -- non-deterministic and a retry re-picks a DIFFERENT cohort
                -- (nmh.11). Ordering by customer_id after mc makes it STABLE.
                ORDER BY mc, customer_id
                LIMIT %s
                """,
                (tenant_id, limit),
            ).fetchall()
    except Exception:
        return []
    ids = [r["customer_id"] for r in rows]
    return lookup_leads(
        tenant_id, [{"customer_id": i} for i in ids], dsn=dsn, memory_store=memory_store
    )


# Keyword families for filtering conversation threads by what the CUSTOMER said.
# Deterministic substring matching over their verbatim words — never a model call,
# never an inferred trait; the returned quote IS the receipt.
_TOPIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "price": ("price", "pricing", "cost", "expensive", "afford", "budget",
              "deposit", "payment", "financ", "money", "$", "cheap"),
    "timing": ("time", "schedul", "busy", "later", "postpon", "resched",
               "next month", "wait", "delay", "travel", "moved", "moving"),
    "trust": ("nervous", "scared", "worried", "pain", "trust", "unsure",
              "not sure", "second thought"),
}


def conversation_lead_index(
    tenant_id: str, *, topic: str | None = None, limit: int = 12,
    dsn: str | None = None,
) -> list[dict[str, Any]]:
    """Index of the customers whose imported conversation threads are on file —
    name/email/turn-count, plus (when ``topic`` is given) the first CUSTOMER turn
    matching that keyword family, quoted verbatim. A topic outside the known
    families is used as a literal substring. Leads whose thread has no matching
    customer turn are excluded when a topic is set. Ordered like the campaign
    cohort (earliest thread first) so 'the first three' here and the run's
    warm-lead picks line up."""
    want = (topic or "").strip().lower()
    kws = _TOPIC_KEYWORDS.get(want) or ((want,) if want else ())
    out: list[dict[str, Any]] = []
    with _connect(dsn) as conn:
        rows = conn.execute(
            """
            SELECT lc.customer_id, c.name, c.email, c.phone, lc.turns
              FROM lead_conversations lc
              JOIN customers c
                ON c.tenant_id = lc.tenant_id AND c.id = lc.customer_id
             WHERE lc.tenant_id = %s
             ORDER BY lc.created_at, lc.customer_id
            """,
            (tenant_id,),
        ).fetchall()
    for r in rows:
        turns = r["turns"] or []
        quote: str | None = None
        if kws:
            for t in turns:
                if (t.get("speaker") or "").lower() != "customer":
                    continue
                low = (t.get("text") or "").lower()
                if any(k in low for k in kws):
                    quote = t.get("text")
                    break
            if quote is None:
                continue
        out.append({
            "customer_id": r["customer_id"],
            "name": r["name"],
            "email": r["email"],
            "phone": r["phone"],
            "turns": len(turns),
            "quote": quote,
        })
        if len(out) >= limit:
            break
    return out


# --------------------------------------------------------------------------- #
# Personalized draft builder — grounded in REAL persona facts only
# --------------------------------------------------------------------------- #


def choose_channel(facts: dict[str, Any], plan_channels: list[str] | None) -> str:
    """Pick the outreach channel for a lead, honestly respecting consent.

    OPERATOR-EXPLICIT channels are authoritative: when ``plan_channels`` is set
    (the operator answered 'email channel' / this is one channel's isolated child
    run), ONLY those channels are candidates — consent still outranks the
    operator (a below-consent email/SMS is never sent to that channel), but the
    lead is NEVER silently diverted to a channel the operator did not ask for
    (an 'sms' child run once staged an instagram DM because the persona
    preference outranked the plan). Returns '' when no requested channel is
    consented — the caller records a counted skip.

    Legacy (no plan channels): persona ``likely_best_channel`` → first
    ``preferred_channels`` → ``instagram``, email/SMS consent-gated with the
    instagram fall-through."""
    traits = facts.get("persona_traits", {})
    explicit = [str(c).strip().lower() for c in (plan_channels or []) if str(c or "").strip()]
    candidates: list[str] = []
    if explicit:
        candidates = list(explicit)
    else:
        best = traits.get("likely_best_channel")
        if best:
            candidates.append(str(best))
        candidates += [str(c) for c in facts.get("preferred_channels", [])]
        candidates.append("instagram")

    # The consent gate is routed through the first-class ``entities.Consent`` (one typed
    # representation with provenance): email/SMS require the opt-in; a below-consent SMS
    # falls through to instagram — never overriding withheld consent.
    from studio.entities import channel_consented

    for ch in candidates:
        ch = ch.strip().lower()
        if ch in ("email", "gmail"):
            if channel_consented(facts, "gmail"):
                return "gmail"
            continue
        if ch == "sms":
            if channel_consented(facts, "sms"):
                return "sms"
            continue
        if ch in ("instagram", "ig"):
            return "instagram"
        if ch == "facebook":
            return "facebook"
    # Explicit channels exhausted without consent: an honest no-channel — the
    # caller records the skip. Only the legacy (no-plan-channel) path keeps the
    # instagram fall-through.
    return "" if explicit else "instagram"


# --------------------------------------------------------------------------- #
# Brand voice + verified research — the REAL inputs the copywriter cell consumes
# --------------------------------------------------------------------------- #

def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _default_tenant() -> str | None:
    """Resolve the fallback sending tenant for a call that passed no ``tenant_id``.

    An explicit deploy-configured ``SCALERS_TENANT_ID`` is always honored — that is a
    real operator choice. The ``ladies8391`` FIXTURE, however, is dev-only: it must
    NEVER silently stand in for a real tenant, or the fixture's voice/claims bleed into
    a real client's outreach. So the fixture fallback is gated behind an explicit dev
    flag (``SCALERS_ALLOW_FIXTURE_TENANT``). With neither set this returns ``None`` and
    callers degrade honestly (empty voice) rather than borrowing the fixture identity.

    See CustomerAcq-wwy.7 (r8: kill ladies8391 fixture bleed).
    """
    configured = os.environ.get("SCALERS_TENANT_ID")
    if configured:
        return configured
    if _env_flag("SCALERS_ALLOW_FIXTURE_TENANT", False):
        return "ladies8391"
    return None


def _llm_copy_enabled() -> bool:
    """Whether to write copy with the REAL copywriter cell (vs the deterministic
    fallback). Honors an explicit ``SCALERS_OUTREACH_LLM`` override; otherwise auto:
    on iff an Anthropic key is present (no key -> honest deterministic copy)."""
    override = os.environ.get("SCALERS_OUTREACH_LLM")
    if override is not None:
        return override.strip().lower() in ("1", "true", "yes", "on")
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _research_enabled(
    deep_research: bool | None, research_depth: str | None = None,
) -> bool:
    """Deep per-lead web research (Firecrawl) is OFF by default — it makes live
    egress and is gated behind an explicit opt-in. Opt-ins, in order:

    * ``deep_research=True`` (the interview's yes/no question), or
    * ``research_depth == "deep"`` (the interview's light/standard/deep question —
      the operator asked for deep homework on each person), or
    * the ``STUDIO_DEEP_RESEARCH`` env flag.

    An EXPLICIT ``deep_research=False`` (the operator said no) always wins — a
    later "deep" depth answer never overrides a stated opt-out into paid egress.
    Backward compatible: ``_research_enabled(x)`` behaves exactly as before. With
    no Firecrawl key wired it degrades to honest-empty regardless."""
    if deep_research is False:
        return False
    if deep_research is True:
        return True
    if (research_depth or "").strip().lower() == "deep":
        return True
    return _env_flag("STUDIO_DEEP_RESEARCH", False)


def _render_voice_context(dims: Any) -> str:
    """Render the tenant's ``VoiceDimensions`` (tone / structure / lexicon / bans /
    policies) into the ``brand_voice_context`` string the copywriter cell consumes.
    Pure projection of writer-owned dimensions — invents no voice content."""
    v = dims.vocabulary

    def _b(items) -> str:
        return "\n".join(f"- {x}" for x in items) if items else "- (none on file)"

    return "\n".join([
        "## Tone:", _b(dims.tone),
        "## Structure:", _b(dims.structure),
        f"## Emoji policy: {v.emoji_policy or '(per brand)'}",
        "## Preferred lexicon:", _b(v.prefer),
        "## NEVER use (hard ban — beats every pattern):", _b(v.ban),
    ])


def resolve_brand_voice(tenant_id: str | None = None) -> tuple[str, tuple[str, ...]]:
    """Resolve ``(brand_voice_context, approved_claims)`` for the SENDING tenant via
    the brand-voice resolver (``config.load_pack`` + ``kb.voice.load_voice_dimensions``).

    These describe the SENDER (the artist writing the outreach), never the recipient.
    Degrades honestly to ``("", ())`` if the pack / dimensions cannot be resolved, OR
    if no tenant is given and no default resolves (:func:`_default_tenant`) — the copy
    then writes from goal + grounded recipient facts only, never borrowing a fixture's
    voice. ``resolve_brand_voice(None)`` is ``("", ())`` unless a real tenant is
    configured or the fixture dev flag is set (r8: kill ladies8391 fixture bleed)."""
    tid = tenant_id or _default_tenant()
    if not tid:
        return "", ()
    try:
        from config.loader import load_pack
        from kb.voice import load_voice_dimensions

        pack = load_pack(tid)
        dims = load_voice_dimensions(pack)
        return _render_voice_context(dims), tuple(dims.vocabulary.approved_claims)
    except Exception:
        return "", ()


_SOCIAL_HOSTS = ("instagram.", "facebook.", "tiktok.", "twitter.", "x.com", "linkedin.")
_LISTING_HOSTS = (
    "yelp.", "google.", "maps.", "foursquare.", "tripadvisor.", "bing.com/maps",
    "booksy.", "fresha.", "thumbtack.", "nextdoor.",
)


def _classify_source(url: str) -> str:
    """Tag a real research URL by source TYPE from its host so the per-lead sources are
    DISTINCT (official website / social profile / public listing), not a blind repeat.
    Pure host inspection — never fabricates the type."""
    host = url.split("//", 1)[-1].split("/", 1)[0].lower()
    if any(h in host for h in _SOCIAL_HOSTS):
        return "social"
    if any(h in host for h in _LISTING_HOSTS):
        return "listing"
    return "website"


def _research_query(facts: dict[str, Any]) -> str:
    """The per-lead web query, shaped by WHAT the lead actually is (65w.13 lesson:
    frame by the lead's REAL type, never a hardcoded business shape).

    * An explicit studio/shop/business lead (:func:`_is_studio_lead`) keeps the
      business-shaped query: ``"{name}" {city} tattoo studio``.
    * Every other lead is a CONSUMER (skindesign's leads are all people), so the
      query is person-shaped — ``"{name}" {city} [first interest] tattoo`` — their
      own name plus their real CSV interest, never a fabricated business framing.

    The name is quoted so a person search stays about THIS person. Pure projection
    of real fields; absent fields simply drop out."""
    name = (facts.get("name") or "").strip()
    city = (facts.get("city") or "").strip()
    if _is_studio_lead(facts):
        return " ".join(x for x in [f'"{name}"', city, "tattoo studio"] if x)
    interest = next(
        (str(i).strip() for i in (facts.get("interests") or []) if str(i).strip()), ""
    )
    return " ".join(x for x in [f'"{name}"', city, interest, "tattoo"] if x)


def research_studio(facts: dict[str, Any], *, enabled: bool) -> list[dict[str, Any]]:
    """Verified web research for the RECIPIENT studio via the wired Firecrawl provider
    (``research.pipeline.live_registry`` — enabled only when a key is present). Returns
    verbatim ``{title, snippet, url, source_type, customer_id}`` hits (cite-only context
    for the copywriter), or ``[]`` honestly when disabled / keyless / no hits.

    DIVERSITY: hits are classified by source TYPE (official website / social profile /
    public listing) and selected to favour DISTINCT types — so a lead surfaces its
    website AND its socials AND its listing rather than three of the same. Each source
    is BOUND to this specific lead via ``customer_id``. If only one source exists, only
    one is returned (no padding).

    HONESTY GATE: every field is copied verbatim from a real provider response; the type
    is derived from the real URL; the binding is the lead's real id. This NEVER fabricates
    a source. A failure degrades to ``[]`` (no citation), never an invented fact.

    SENSITIVE-TRAIT FILTER (spec §7/§24): before a hit is returned (and later persisted
    by the caller), its title/snippet pass the deterministic protected-traits filter —
    a line asserting the lead's gender/age/ethnicity/health/religion/sexuality/financial
    status/immigration status/politics is blanked unless the customer's own first-party
    data provides it; a hit with no citable text left is dropped. Each blank is recorded
    on the hit as ``trait_filtered`` so the persisted source is honest about the scrub."""
    if not enabled:
        return []
    name = (facts.get("name") or "").strip()
    if not name:
        return []
    cust_id = facts.get("customer_id")
    try:
        from research.pipeline import live_registry
        from research.protected_traits import (
            allowed_categories,
            build_first_party_corpus,
            filter_lines,
        )

        provider = live_registry().get("firecrawl")
        if provider is None or not getattr(provider, "enabled", False):
            return []
        query = _research_query(facts)
        allowed = allowed_categories(facts)
        fp_corpus = build_first_party_corpus(facts)
        # Collect more than we keep so we can diversify across source types.
        raw: list[dict[str, Any]] = []
        seen: set[str] = set()
        for hit in provider.search(query, limit=6):
            url = getattr(hit, "url", None)
            if not url or url in seen:
                continue
            seen.add(url)
            entry: dict[str, Any] = {
                "title": getattr(hit, "title", None),
                "snippet": getattr(hit, "snippet", None),
                "url": url,
                "source_type": _classify_source(url),
                "customer_id": cust_id,
            }
            # Protected-traits scrub on the verbatim text fields (blank, never rewrite).
            scrubbed: list[str] = []
            for fld in ("title", "snippet"):
                val = entry.get(fld)
                if not val:
                    continue
                clean, drops = filter_lines(
                    str(val), allowed=allowed, first_party_corpus=fp_corpus
                )
                if drops:
                    entry[fld] = clean or None
                    scrubbed.extend(
                        f"{fld}:{d.get('categories', '')}" for d in drops
                    )
            if scrubbed:
                entry["trait_filtered"] = scrubbed
            if not (entry.get("title") or entry.get("snippet")):
                continue  # nothing citable survived the trait filter -> drop the hit
            raw.append(entry)
        # Diversify: one pass takes the FIRST hit of each distinct type (website first so
        # the lead's own positioning leads), then fills remaining slots in rank order.
        ordered: list[dict[str, Any]] = []
        used_idx: set[int] = set()
        for want in ("website", "social", "listing"):
            for i, r in enumerate(raw):
                if i not in used_idx and r["source_type"] == want:
                    ordered.append(r)
                    used_idx.add(i)
                    break
        for i, r in enumerate(raw):
            if i not in used_idx:
                ordered.append(r)
                used_idx.add(i)
        return ordered[:3]
    except Exception:
        return []


def gather_social_context(facts: dict[str, Any], enabled: bool = False) -> str | None:
    """CONSENT-SAFE public social context for ONE lead (spec §7) — or honest ``None``.

    PRIVACY LINE: only a handle the CUSTOMER THEMSELVES provided on their own row
    (``ig_handle`` / ``linkedin_handle``) is ever looked up — NEVER name-based social
    discovery. One gated Firecrawl site-search for that handle; the first hit with
    usable public text is returned as a SHORT extract with its URL cited inline (so
    the psych profile's evidence carries the real source). Before anything is
    returned it passes the deterministic protected-traits filter — a line asserting
    the lead's gender/age/ethnicity/health/religion/sexuality/financial status/
    immigration status/politics is scrubbed (unless customer-provided first-party
    data backs it).

    Honest-none everywhere: disabled / no handle / keyless / provider disabled /
    blocked egress (the outbound proxy blocks some hosts) / no public hit / nothing
    left after the trait filter -> ``None``, never a fabricated social signal. Pure
    aside from the single gated provider call."""
    if not enabled:
        return None
    ig = str(facts.get("ig_handle") or "").strip().lstrip("@")
    li = str(facts.get("linkedin_handle") or "").strip().lstrip("@")
    if not ig and not li:
        return None
    query = (
        f"site:instagram.com {ig}" if ig else f"site:linkedin.com/in {li}"
    )
    try:
        from research.pipeline import live_registry

        provider = live_registry().get("firecrawl")
        if provider is None or not getattr(provider, "enabled", False):
            return None
        hits = provider.search(query, limit=3)
    except Exception:
        return None  # blocked host / proxy / provider failure -> honest none

    from research.protected_traits import (
        allowed_categories,
        build_first_party_corpus,
        filter_lines,
    )

    allowed = allowed_categories(facts)
    fp_corpus = build_first_party_corpus(facts)
    for hit in hits or []:
        url = str(getattr(hit, "url", None) or "").strip()
        text = " — ".join(
            x for x in (
                str(getattr(hit, "title", None) or "").strip(),
                str(getattr(hit, "snippet", None) or "").strip(),
            ) if x
        )
        if not url or not text:
            continue
        clean, _drops = filter_lines(
            text, allowed=allowed, first_party_corpus=fp_corpus
        )
        clean = " ".join(clean.split()).strip()
        if not clean:
            continue  # everything public asserted protected traits -> not usable
        return f"{clean[:400]} (source: {url})"
    return None


def _first_research_signal(
    research: list[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    """The first verified research hit carrying usable verbatim text (snippet or title)
    AND a url about THIS studio — the strongest, most lead-specific differentiator.
    Honest: returns ``None`` when research is empty/disabled (never fabricates one)."""
    for r in research or []:
        text = (r.get("snippet") or r.get("title") or "").strip()
        if text and (r.get("url") or "").strip():
            return r
    return None


# --------------------------------------------------------------------------- #
# Category + objection branching — the psych-profile-driven angle (P1 #3).
#
# When a grounded :class:`~studio.psych_profile.PsychProfile` is available, the WHY a
# warm lead did not convert (their objection) is the most lead-specific angle there is,
# so it leads. Each objection maps to an honest response; a price/payment angle may only
# reference a REAL substantiated offer (passed in), never an invented discount.
# --------------------------------------------------------------------------- #
def _pf(profile: Any, field: str) -> tuple[str, str, str]:
    """Read ``(value, signal, evidence)`` from a PsychProfile field, tolerating either a
    pydantic object or a plain dict. Returns empties when absent."""
    f = getattr(profile, field, None)
    if f is None and isinstance(profile, dict):
        f = profile.get(field)
    if f is None:
        return "", "", ""
    if isinstance(f, dict):
        return str(f.get("value") or ""), str(f.get("signal") or ""), str(f.get("evidence") or "")
    return (str(getattr(f, "value", "") or ""), str(getattr(f, "signal", "") or ""),
            str(getattr(f, "evidence", "") or ""))


# (objection -> (angle key, human label, one-line honest guidance)).
_OBJECTION_ANGLES: dict[str, tuple[str, str, str]] = {
    "price": ("addressing-price", "their price hesitation",
              "acknowledge the budget and offer a real lower-commitment option"),
    "payment": ("payment-flexibility", "a flexible payment path",
                "offer a real deposit / payment-split path"),
    "timing": ("flexible-timing", "their timing hesitation",
               "keep it low-pressure and flexible for whenever they're ready"),
    "trust": ("proof-and-portfolio", "building trust with real proof",
              "reassure with real healed work / first-timer care, no hype"),
    "uncertainty": ("low-pressure-consult", "a no-pressure way to decide",
                    "offer a relaxed consult to help them decide, no push"),
    "trust_concern": ("rebuild-trust", "repairing trust after a rough booking experience",
                      "acknowledge the past experience without restating painful details; "
                      "direct-artist commitment, no-reschedule guarantee, manager point "
                      "of contact — never a hard sell"),
    "blocked_by_prereq": ("prereq-help", "the prerequisite step that blocks their booking",
                          "a helpful next-step note on the prerequisite (e.g. laser "
                          "removal guidance) — explicitly not a discount pitch"),
    "went_quiet_mid_booking": ("resume-booking",
                               "picking the booking back up where it stopped",
                               "low-pressure pick-up-where-we-left-off, referencing the "
                               "exact step they stopped at"),
}
# (category -> angle) for the non-objection lifecycle branches.
_CATEGORY_ANGLES: dict[str, tuple[str, str]] = {
    "recurring-customer": ("loyalty-touchup", "a loyalty / touch-up invite"),
    "converted-but-unpaid": ("completion-nudge", "a gentle nudge to finish the booking"),
    "past-customer-reactivation": ("win-back", "a warm win-back note"),
}


def _objection_angle(profile: Any, offer: Any) -> dict[str, Any] | None:
    """The objection/category-driven angle from a GROUNDED profile, or None when the
    profile carries no actionable grounded signal (caller then uses the base ranking).

    The angle's ``basis`` is a REAL span: the objection's evidence quote, plus the real
    offer's terms when one was substantiated. Never invents a discount — if ``offer`` is
    None the angle addresses the objection without a code."""
    if profile is None:
        return None
    obj_val, obj_sig, obj_ev = _pf(profile, "primary_objection")
    grounded_obj = obj_val and obj_val != "none-found" and obj_sig in ("stated", "inferred")

    if grounded_obj and obj_val in _OBJECTION_ANGLES:
        key, label, _guide = _OBJECTION_ANGLES[obj_val]
        if offer is not None and obj_val in ("price", "payment"):
            label = f"their {obj_val} hesitation + a real offer ({offer.code})"
            basis = f'objection "{obj_ev[:110]}" -> offer {offer.as_evidence()}'
            key = "offer-" + ("discount" if obj_val == "price" else "payment")
        # The thread-shape labels quote the REAL thread (often a studio turn), so the
        # basis names what the quote IS — never "their stated words" for a studio line.
        elif obj_val == "went_quiet_mid_booking":
            basis = f'the exact step the thread stopped at: "{obj_ev[:130]}"'
        elif obj_val == "blocked_by_prereq":
            basis = f'the prerequisite stated in the thread: "{obj_ev[:130]}"'
        else:
            basis = f'their stated {obj_val} hesitation: "{obj_ev[:130]}"'
        return {"key": key, "label": label, "basis": basis,
                "inferred": obj_sig == "inferred", "generic": False}

    # No objection -> a lifecycle/category angle when the category is actionable.
    cat_val, cat_sig, cat_ev = _pf(profile, "umbrella_category")
    if cat_val in _CATEGORY_ANGLES and cat_sig in ("stated", "inferred"):
        key, label = _CATEGORY_ANGLES[cat_val]
        basis = cat_ev or f"category={cat_val}"
        if offer is not None and cat_val in ("recurring-customer", "past-customer-reactivation"):
            label += f" + a real offer ({offer.code})"
            basis = f"{basis} -> offer {offer.as_evidence()}"
            key = "offer-" + key
        return {"key": key, "label": label, "basis": basis,
                "inferred": cat_sig == "inferred", "generic": False}
    return None


def _choose_angle(
    facts: dict[str, Any], research: list[dict[str, Any]] | None,
    *, profile: Any = None, offer: Any = None,
) -> dict[str, Any]:
    """Pick ONE distinct outreach angle for this lead from REAL differentiators only.

    Ranked most-lead-specific first: verified research positioning -> real past-work
    from our own history -> shared craft (CSV interest, else inferred persona lean) ->
    re-engagement (lifecycle / win-back persona signal) -> the CSV note -> light local
    (city) -> honest-generic. Returns ``{key, label, basis, inferred, generic}`` where
    ``basis`` is the verbatim real fact the angle stands on, ``inferred`` flags a
    persona-derived (not hard) signal, and ``generic`` is True ONLY when the lead has
    NO distinguishing data — in which case we say so honestly rather than fake
    personalization. NEVER invents a differentiator.

    When a grounded psych ``profile`` is supplied (the warm-lead path), the lead's
    OBJECTION / lifecycle category is the most lead-specific angle and LEADS — before the
    base research/history ranking below. A price/payment angle references the passed
    ``offer`` ONLY when it is a real substantiated offer; otherwise it addresses the
    objection without inventing a discount."""
    objection_angle = _objection_angle(profile, offer)
    if objection_angle is not None:
        return objection_angle

    traits = facts.get("persona_traits", {}) or {}
    interests = facts.get("interests", []) or []
    city = (facts.get("city") or "").strip()
    notes = (facts.get("notes") or "").strip()
    lifecycle = traits.get("lifecycle_stage")
    win_back = bool(traits.get("win_back_candidate"))
    tattoos = facts.get("tattoo_history", []) or []
    last_style = tattoos[0]["style"] if tattoos and tattoos[0].get("style") else None
    aesthetic = traits.get("aesthetic_lean")
    csv_interest = interests[0] if interests else None

    sig = _first_research_signal(research)
    if sig is not None:
        snippet = (sig.get("snippet") or sig.get("title") or "").strip()
        short = snippet if len(snippet) <= 140 else snippet[:137] + "..."
        return {
            "key": "their-positioning", "label": "their own public positioning",
            "basis": f'"{short}" ({sig["url"]})', "inferred": False, "generic": False,
        }
    if last_style:
        return {
            "key": "past-work", "label": f"their past {last_style} work with us",
            "basis": f"past {last_style} piece on file", "inferred": False, "generic": False,
        }
    if csv_interest:
        return {
            "key": "shared-craft", "label": f"a shared interest in {csv_interest}",
            "basis": f"interest on file: {csv_interest}", "inferred": False, "generic": False,
        }
    if aesthetic:
        return {
            "key": "shared-craft", "label": f"a {aesthetic} aesthetic lean",
            "basis": f"persona aesthetic_lean={aesthetic}", "inferred": True, "generic": False,
        }
    if win_back or lifecycle in ("lapsing", "lead-no-visit", "churn-risk"):
        return {
            "key": "re-engagement", "label": "a re-engagement / win-back note",
            "basis": f"persona lifecycle={lifecycle or 'win-back candidate'}",
            "inferred": True, "generic": False,
        }
    if notes:
        return {
            "key": "csv-note", "label": "the note on our list",
            "basis": f"note: {notes}", "inferred": False, "generic": False,
        }
    if city:
        return {
            "key": "local", "label": f"a light local connection ({city})",
            "basis": f"city={city}", "inferred": False, "generic": False,
        }
    return {
        "key": "generic", "label": "an honest general introduction",
        "basis": "no distinguishing research or history on file",
        "inferred": False, "generic": True,
    }


def _real_signals_on_file(
    facts: dict[str, Any], research: list[dict[str, Any]] | None, *, exclude_key: str,
) -> list[str]:
    """The OTHER real, traceable differentiators on file beyond the chosen angle — named
    so the operator can verify each one against real data. Excludes the signal the angle
    already stands on. REAL-only: nothing is added that isn't a real DB/CSV/research fact."""
    traits = facts.get("persona_traits", {}) or {}
    interests = facts.get("interests", []) or []
    tattoos = facts.get("tattoo_history", []) or []
    last_style = tattoos[0]["style"] if tattoos and tattoos[0].get("style") else None
    aesthetic = traits.get("aesthetic_lean")
    city = (facts.get("city") or "").strip()
    notes = (facts.get("notes") or "").strip()

    extras: list[str] = []
    if last_style and exclude_key != "past-work":
        extras.append(f"past {last_style} piece")
    if interests and exclude_key != "shared-craft":
        extras.append(f"interest: {interests[0]}")
    elif aesthetic and exclude_key != "shared-craft":
        extras.append(f"aesthetic lean: {aesthetic} (inferred)")
    if notes and exclude_key != "csv-note":
        extras.append(f"note: {notes}")
    if city and exclude_key != "local":
        extras.append(f"city: {city}")
    n_research = sum(1 for r in (research or []) if (r.get("url") or "").strip())
    if n_research and exclude_key != "their-positioning":
        extras.append(f"{n_research} verified web source(s)")
    return extras


def _angle_rationale(
    angle: dict[str, Any], facts: dict[str, Any],
    research: list[dict[str, Any]] | None, name: str,
) -> str:
    """One honest, SPECIFIC sentence: why THIS draft differs from the others — the
    distinct angle chosen for this lead, the real basis it stands on, and the other real
    signals on file (each traceable to real data). A generic lead is labeled honestly,
    and a lead whose only signal is shared (e.g. city-only) says so rather than inventing
    a distinction."""
    who = (name or "this lead").strip()
    if angle["generic"]:
        return (
            f"Honest-generic: no distinguishing research or history on file for {who}, "
            "so this draft stays a general introduction rather than faking personalization."
        )
    qualifier = " (inferred from persona, not a hard fact)" if angle["inferred"] else ""
    msg = f"Personalized on {angle['label']}{qualifier}; grounded on {angle['basis']}."
    extras = _real_signals_on_file(facts, research, exclude_key=angle["key"])
    if extras:
        msg += " Also on file: " + "; ".join(extras) + "."
    elif angle["key"] == "local":
        msg += " This is the only differentiating signal on file for this lead."
    return msg


def _offer_prompt_block(offer: Any, objection: str) -> list[str]:
    """The REAL-offer block for the copywriter prompt (or the no-fabrication guard when
    there is no offer). An offer is quoted EXACTLY; a price/payment objection with no real
    offer is answered honestly, never with an invented discount.

    trust_concern / blocked_by_prereq are NEVER sold to — even a real substantiated
    offer is withheld (a promo after a refund dispute, or instead of the prerequisite,
    is exactly the hard sell those angles forbid)."""
    if objection == "trust_concern":
        return [
            "# TRUST-REPAIR GUARD: this customer had a bad prior experience with US. Do "
            "NOT hard-sell, and do NOT mention any promo, discount, code, or urgency. "
            "Briefly acknowledge the past experience WITHOUT restating its painful "
            "details, commit that their artist will work with them directly, that a "
            "booked date will not be rescheduled on our end, and offer the manager as "
            "their direct point of contact.",
        ]
    if objection == "blocked_by_prereq":
        return [
            "# PREREQUISITE GUARD: a real prerequisite blocks this booking. Write a "
            "HELPFUL next-step message about that prerequisite (what it is, what to do "
            "first) — explicitly NOT a discount pitch. Do not mention any offer, code, "
            "or price.",
        ]
    if offer is not None:
        terms = ", ".join(
            x for x in [
                offer.discount, (f"applies to {', '.join(offer.applies_to)}" if offer.applies_to else ""),
                (f"valid until {offer.valid_until}" if offer.valid_until else ""),
            ] if x
        )
        return [
            "# REAL OFFER YOU MAY REFERENCE (exactly ONE, quote it EXACTLY — do NOT change "
            "the code, percentage, or terms, and do NOT invent any other discount):",
            f"- Code {offer.code}: {offer.description}" + (f" ({terms})" if terms else ""),
        ]
    if objection in ("price", "payment"):
        return [
            "# OFFER GUARD: there is NO real discount/code on file for this lead. Do NOT "
            "invent a discount, code, percentage, or payment plan. Acknowledge the "
            "budget/payment honestly and offer a genuine low-commitment next step (a "
            "smaller/flash piece, or simply a reply to talk options) — no fabricated number.",
        ]
    return []


def _build_email_prompt(
    facts: dict[str, Any], *, goal: str, research: list[dict[str, Any]],
    angle: dict[str, Any], offer: Any = None, profile: Any = None,
    artist_voice: str | None = None,
) -> str:
    """Assemble the copywriter run prompt. It exposes the lead's REAL grounded facts
    (name / city / CSV note / first-party interests + past work from our records /
    cite-only research), threads in the DISTINCT per-lead angle to lead with, and
    hard-forbids asserting anything the system cannot substantiate about a REAL
    business. Persona-inferred signals are passed as clearly-marked soft impressions,
    never as hard facts.

    When a grounded ``profile`` is present (the warm-lead path) the recipient is reframed
    as a WARM LEAD / past customer of the studio (not a peer studio), the objection they
    voiced is surfaced as context, and any REAL ``offer`` is the ONLY discount that may be
    mentioned. Personalization stays ethical — never 'I looked at your Instagram'."""
    # wwy.7 r8 follow-through: a psych profile is ALWAYS produced (deterministic floor
    # read), so profile-PRESENCE is not relationship evidence. Warm/past-customer
    # framing must gate on the SAME evidence the personalization guard accepts — real
    # tattoo history, a relationship-implying lifecycle, a win-back persona signal, or
    # a REAL prior conversation — else every name+email-only lead is framed as a
    # "re-engagement ... past customer" and the guard (rightly) rejects the copy,
    # bleeding the run's quota.
    from cells.personalization_guard import (
        _had_conversation,
        _normalize_lifecycle,
        _RELATIONSHIP_LIFECYCLES,
    )

    _traits0 = facts.get("persona_traits") or {}
    _lifecycle0 = _traits0.get("lifecycle_stage") or facts.get("lifecycle_stage")
    _relationship_evidence = (
        bool(facts.get("tattoo_history"))
        or _normalize_lifecycle(_lifecycle0) in _RELATIONSHIP_LIFECYCLES
        or bool(_traits0.get("win_back_candidate"))
        or _had_conversation(profile)
    )
    warm = profile is not None and _relationship_evidence
    # FLAG A fix (65w.13, wired by ju1.4): peer-studio (B2B) framing must gate on the
    # lead's REAL type, not merely on profile-presence — else a COLD CONSUMER lead (no
    # psych profile yet, a normal person) was told the model it was writing "to a REAL
    # tattoo studio". skindesign customers are ALL consumers. Mirrors the deterministic
    # twin _template_outreach, which already gates on _is_studio_lead. Unknown/blank
    # customer_type -> False -> person framing (fail-safe).
    is_studio = _is_studio_lead(facts)
    objection = ""
    if warm:
        objection, _sig, _ = _pf(profile, "primary_objection")
    name = (facts.get("name") or "the studio").strip()
    city = (facts.get("city") or "").strip()
    notes = (facts.get("notes") or "").strip()
    traits = facts.get("persona_traits", {}) or {}
    interests = facts.get("interests", []) or []
    tattoos = facts.get("tattoo_history", []) or []
    last_style = tattoos[0]["style"] if tattoos and tattoos[0].get("style") else None
    aesthetic = traits.get("aesthetic_lean")
    lifecycle = traits.get("lifecycle_stage")
    win_back = bool(traits.get("win_back_candidate"))

    known: list[str] = [f"- Name: {name}"]
    if city:
        known.append(f"- City / location: {city}")
    if notes:
        known.append(f"- Note on file (from our list): {notes}")
    if interests:
        known.append(
            "- Interests on file (our own records): "
            + ", ".join(str(i) for i in interests[:4])
        )
    if last_style:
        known.append(f"- Past work with us (our own records): a {last_style} piece")

    inferred: list[str] = []
    if aesthetic:
        inferred.append(f"- Aesthetic lean: {aesthetic}")
    if lifecycle:
        inferred.append(f"- Lifecycle stage: {lifecycle}")
    if win_back:
        inferred.append("- Flagged a win-back candidate")
    inferred_block = (
        ["", "# SOFT PERSONA SIGNALS (INFERRED — reference gently as an impression, "
         "NEVER assert as an established fact about them):", *inferred]
        if inferred else []
    )

    if research:
        research_lines = "\n".join(
            f'- "{(r.get("snippet") or r.get("title") or "").strip()}" (source: {r["url"]})'
            for r in research
            if (r.get("snippet") or r.get("title"))
        ) or "- (none usable)"
    else:
        research_lines = "- (none — no verified research available for this studio)"

    # Evidence-cited PUBLIC-WEB ENRICHMENT (operator-initiated lookup, stored as a
    # customer memory): surfaced clearly labeled WITH its URLs so the draft can
    # cite real public facts (their business, their public creative interests)
    # instead of guesses. Honest-empty ([]) when the lead has no enrichment memory.
    try:
        from studio.lead_enrichment import enrichment_prompt_lines

        enrichment_block = enrichment_prompt_lines(facts)
    except Exception:
        enrichment_block = []

    # Prior-relationship evidence: real history on file (tattoos / lifecycle / win-back
    # persona signal) or a real prior conversation (the grounded warm-lead profile).
    # This gates BOTH the goal line and the first-contact rule below.
    # Mirrors the personalization guard's grounds exactly: bare lifecycle values like
    # "lead-no-visit" (never visited) are NOT relationship evidence — only the
    # relationship-implying lifecycles are (wwy.7 r8).
    has_relationship = _relationship_evidence

    goal_line = (goal or "open a genuine conversation").strip()
    # SMOKING-GUN FIX (wwy.7 r8): the operator's INTERNAL goal ("win back lapsed
    # clients") was spliced verbatim into the prompt for leads whose row carries
    # name+email ONLY — telling the model the recipient is a lapsed client, so it
    # fabricated an implied history ("work with you again") the DB cannot back. When
    # there is NO prior-relationship evidence, a relationship-implying goal is
    # reframed as an honest first contact; the campaign-level goal stays internal.
    if not has_relationship and re.search(
        r"win[- ]?back|re-?engage|lapsed|return|reactivat", goal_line, re.IGNORECASE
    ):
        goal_line = (
            "invite them to start a conversation (our records show NO prior "
            "relationship with this person — write it as a genuine first contact)"
        )

    if angle["generic"]:
        angle_block = [
            "# YOUR ANGLE FOR THIS RECIPIENT:",
            "- We have NO distinguishing research or history for this lead. Do NOT "
            "manufacture specifics. Write an honest, warm GENERAL introduction that "
            "leans only on the true reason for reaching out — one studio to another — "
            "plus their name and city. Honest-general beats fake-personal.",
        ]
    else:
        angle_block = [
            "# YOUR DISTINCT ANGLE FOR THIS RECIPIENT "
            "(lead with THIS — do not write an interchangeable template):",
            f"- Angle: {angle['label']}.",
            f"- Grounded on: {angle['basis']}.",
            "- Open on this specific angle so this email could only have been written "
            "to THIS recipient. Stay strictly within the facts/signals above.",
        ]

    if warm:
        intro = (
            "You are writing ONE short, warm, PERSONAL re-engagement message, in the "
            "BRAND VOICE above, to a WARM LEAD / past customer of YOUR OWN studio — "
            "someone who previously enquired or visited. Write it as a genuine, human "
            "follow-up that could only have been written to THIS person; never a template."
        )
    elif is_studio:
        intro = (
            "You are writing ONE short, warm cold-outreach EMAIL, in the BRAND VOICE "
            "above, to a REAL tattoo studio (the recipient). Treat this as a genuine first "
            "introduction (purpose = intro), and make it UNMISTAKABLY for this specific "
            "recipient — not a template with the name swapped."
        )
    else:
        intro = (
            "You are writing ONE short, warm cold-outreach EMAIL, in the BRAND VOICE "
            "above, to a REAL PERSON — a prospective client (the recipient), NOT a "
            "business or studio. Treat this as a genuine first introduction (purpose = "
            "intro), and make it UNMISTAKABLY for this specific person — not a template "
            "with the name swapped. NEVER address or describe them as a studio/shop/"
            "business, and never imply they run one."
        )

    # Warm-lead context: the objection they voiced + where they sit + the honest angle,
    # so the copy addresses the real reason they didn't book (grounded, never invented).
    objection_block: list[str] = []
    if warm:
        where = getattr(profile, "where_customer_sits", "") if not isinstance(profile, dict) else profile.get("where_customer_sits", "")
        _obj_val, _obj_sig, obj_ev = _pf(profile, "primary_objection")
        obj_bits = []
        if where:
            obj_bits.append(f"- Where this customer sits: {where}.")
        if objection and objection != "none-found" and obj_ev:
            # The thread-shape labels quote the thread (often a studio turn) — framed as
            # what they ARE, never presented as the customer's own words.
            if objection == "went_quiet_mid_booking":
                obj_bits.append('- They were actively booking and went quiet; the exact '
                                f'step the thread stopped at (verbatim): "{obj_ev[:160]}".')
                obj_bits.append("- Pick the thread back up at that step, low-pressure — "
                                "make continuing effortless and NEVER guilt them for "
                                "going quiet.")
            elif objection == "blocked_by_prereq":
                obj_bits.append('- Their booking is blocked by a prerequisite, stated in '
                                f'the real thread: "{obj_ev[:160]}".')
                obj_bits.append("- Be genuinely helpful about that prerequisite (what to "
                                "do first and how) — a next-step note, never a sales "
                                "pitch around it.")
            else:
                obj_bits.append(f'- Their hesitation ({objection}), in their own words: "{obj_ev[:160]}".')
                obj_bits.append("- Address that hesitation directly and warmly. Do NOT restate "
                                "their private words back verbatim, and NEVER imply you inspected "
                                "their social media — write naturally.")
        if obj_bits:
            objection_block = ["", "# WHY THEY DIDN'T BOOK (real, grounded — speak to this):", *obj_bits]

    offer_block = _offer_prompt_block(offer, objection)
    offer_block = (["", *offer_block] if offer_block else [])

    recipient_word = "customer" if warm else "recipient"
    return "\n".join([
        intro,
        "",
        f"# WHAT YOU ACTUALLY KNOW ABOUT THE {recipient_word.upper()}",
        "# (hard facts you may state about them):",
        *known,
        *inferred_block,
        *objection_block,
        *offer_block,
        "",
        f"# RESEARCH (verbatim web snippets about the {recipient_word} — cite-only context):",
        research_lines,
        *enrichment_block,
        "",
        *angle_block,
        "",
        "# HARD GROUNDING RULES — no fabrication:",
        "- You may reference ONLY the hard facts above, the SOFT signals (marked as "
        "impressions, never as fact), the REAL offer above if one is listed, and a "
        "research or public-web-enrichment snippet ONLY when it is unmistakably "
        "about them.",
        f"- Do NOT invent or imply anything NOT listed above about the {recipient_word}'s "
        "style, artists, awards, reputation, clientele, discounts, or history. If a "
        "specific is missing, stay general and honest rather than guessing.",
        # ju1.3 anti-fake-personalization: never CLAIM a per-customer signal we don't have.
        "- NEVER claim you saw their Instagram/social, know their tattoo interests, their "
        "past bookings/last tattoo, their favourite artist, or their objection UNLESS that "
        "exact fact is listed above for THIS person. With no such fact, do not reference "
        "it at all — a deterministic guard rejects any draft that fakes this.",
        # A recorded SMS opt-out (reply-STOP honored) must be visible to the strategy:
        # the draft may never propose texting as the channel or the follow-up.
        *(["- CHANNEL GUARD: SMS suppressed — email only. This customer opted out of "
           "SMS (reply-STOP honored). Never propose texting them, and never mention "
           "SMS as a follow-up channel."]
          if facts.get("sms_opt_in") is False else []),
        "- Everything you say about YOURSELF (the sender) must come from the brand "
        "voice's approved claims above — nothing else.",
        # SIGN-OFF IDENTITY GATE (truth-gap fix: a draft signed 'Cheers, Keebs' for a
        # lead with no Keebs link). Signing as a named artist is allowed ONLY when the
        # operator explicitly set the campaign artist or this lead's own record links
        # them to that artist — otherwise the draft signs as the studio, period.
        *([
            f"- SIGN-OFF IDENTITY: you may write/sign this message as {artist_voice} "
            "(this artist fronts the campaign for this recipient — an operator-set "
            "campaign artist or the recipient's own recorded artist). Never sign as "
            "any OTHER individual artist.",
        ] if artist_voice else [
            "- SIGN-OFF IDENTITY: sign as the studio (the brand voice above) ONLY. "
            "Do NOT sign as, or write in the first-person voice of, ANY individual "
            "artist by name — this recipient has no recorded link to a specific "
            "artist and the operator did not set one; naming an artist as the "
            "sender would fabricate a relationship.",
        ]),
        # wwy.7 r8 (smoking gun): with NO prior-relationship evidence on file, the copy
        # must read as a genuine first contact — never a fabricated reunion.
        *([] if has_relationship else [
            "- OUR RECORDS SHOW NO PRIOR RELATIONSHIP with this person: no past visit, "
            "no tattoo, no conversation. This is a FIRST contact. Do NOT imply any "
            "shared history — no 'again', no 'welcome back', no 'it's been a while', "
            "no 'work with you again'. A deterministic guard rejects any draft that "
            "implies a relationship we cannot substantiate.",
        ]),
        f"- Reason for reaching out / goal: {goal_line}.",
        "",
        "Write a specific honest subject and a short plain-text body. Include ONE clear "
        "call to action — a single concrete next step the recipient can take ("
        + (f"point them to the booking link {_booking_link()}" if _booking_link()
           else "since there is no booking link, invite a reply, e.g. 'reply yes and "
                "I'll send over a booking link'")
        + ").",
        "End the body with a brief visible unsubscribe line containing the exact "
        "token {{unsubscribe}} (the staging layer resolves it to a real reply-based "
        "opt-out before the draft is queued).",
    ])


def _offer_phrase(offer: Any) -> str:
    """A short, honest phrase mentioning a REAL offer's code + terms (deterministic path).
    Empty when there is no offer — never invents a discount."""
    if offer is None:
        return ""
    disc = f" ({offer.discount})" if offer.discount else ""
    return f" We've got a small offer that might help — {offer.description}{disc}, code {offer.code}."


# The closed set of customer_type values that mark a lead as an actual business /
# peer studio. EXACT-token membership, not substring: a consumer row whose type merely
# CONTAINS a B2B word ("studio walk-in", "business owner", "partner referral") must
# stay customer-framed — substring matching failed open in the embarrassing direction.
_STUDIO_LEAD_TYPES = frozenset({
    "studio", "tattoo studio", "shop", "tattoo shop", "b2b", "partner", "business",
})


def _is_studio_lead(facts: dict[str, Any]) -> bool:
    """True ONLY when the lead's ``customer_type`` explicitly marks it as a studio /
    shop / business partner — the one case peer-studio (B2B) framing is legitimate.
    Unknown or blank types default to CUSTOMER framing (65w.13 bug 3, fail-safe: a
    studio greeted like a customer reads merely generic; a past customer greeted as
    a peer studio is a client-facing embarrassment)."""
    ct = " ".join(str(facts.get("customer_type") or "").strip().lower().split())
    return ct in _STUDIO_LEAD_TYPES


def _resolve_sender_city(tenant_id: str | None) -> str | None:
    """The SENDER studio's real city for location phrasing ("we're local", "fellow
    {city} studio"), resolved from tenant/pack config — or ``None`` (FLAG B, 65w.13).

    Honest posture, never fabricate: a tenant's sender city is used ONLY when the pack
    config carries a real single-location city. A MULTI-LOCATION tenant (e.g. skindesign:
    Spring Mountain / OC / Soho / Hawaii / NY / Nashville) has no single canonical sender
    city, so this returns ``None`` and the location phrase is omitted (the safe default).
    This is the one production caller that gives ``_template_outreach``'s ``sender_city``
    a real resolution path; it is NEVER derived from the recipient row (``facts['city']``,
    bug 2). When a single-location tenant's real city is added to pack config, read it here."""
    if not tenant_id:
        return None
    try:
        from config.loader import load_pack

        pack = load_pack(tenant_id)
        city = getattr(pack, "sender_city", None)
        return city.strip() if isinstance(city, str) and city.strip() else None
    except Exception:
        return None


def _template_outreach(
    facts: dict[str, Any], *, goal: str, ch: str, angle: dict[str, Any], offer: Any = None,
    sender_city: str | None = None,
) -> tuple[str | None, str]:
    """Deterministic fallback copy (no model): honest, grounded only in real facts,
    and SHAPED BY THE PER-LEAD ANGLE so two leads do not collapse to one template.

    Used when LLM copy is disabled (no Anthropic key) or the cell fails. Still never
    invents a recipient detail — opener, detail, and subject are keyed off the angle
    chosen from this lead's real differentiators (and honestly generic when thin). An
    objection angle references the REAL ``offer`` (code + terms) ONLY when one was passed;
    with no offer it stays a genuine low-commitment nudge, never a fabricated discount.

    COPY-SAFETY (65w.13): ``goal`` is the operator's INTERNAL objective — it is
    deliberately never spliced into the customer-facing body (bug 1). The sender's
    location comes ONLY from ``sender_city`` (tenant/studio config); when unknown the
    phrase is omitted — the recipient's own city is never presented as ours (bug 2).
    Peer-studio ("one studio to another") framing is used ONLY for an explicit studio
    lead (:func:`_is_studio_lead`); every other lead gets customer framing (bug 3)."""
    name = (facts.get("name") or "there").strip()
    first = name.split()[0] if name else "there"
    traits = facts.get("persona_traits", {}) or {}
    interests = facts.get("interests", []) or []
    aesthetic = traits.get("aesthetic_lean")
    top_interest = interests[0] if interests else aesthetic
    city = facts.get("city")
    notes = (facts.get("notes") or "").strip()
    tattoos = facts.get("tattoo_history", []) or []
    last_style = tattoos[0]["style"] if tattoos and tattoos[0].get("style") else None

    # Sender location: ONLY the explicitly-provided sender city (never facts["city"],
    # which is the RECIPIENT's). Unknown -> omit the phrase entirely, never fabricate.
    _sender = (sender_city or "").strip()
    city_phrase = f" over in {_sender}" if _sender else ""
    # "We're local" claims are honest only when the sender's city is known AND matches.
    same_city = bool(_sender) and bool(city) and _sender.lower() == str(city).strip().lower()
    is_studio = _is_studio_lead(facts)
    key = angle["key"]
    offer_phrase = _offer_phrase(offer)
    # A warm-lead objection/category angle writes to a CUSTOMER, not a peer studio; the
    # closing "I run a small studio…" line is suppressed for these.
    warm_key = key in (
        "addressing-price", "offer-discount", "payment-flexibility", "offer-payment",
        "flexible-timing", "proof-and-portfolio", "low-pressure-consult",
        "loyalty-touchup", "offer-loyalty-touchup", "completion-nudge",
        "win-back", "offer-win-back",
        "rebuild-trust", "prereq-help", "resume-booking",
    )

    # Opener + detail are keyed off the distinct angle so the deterministic path also
    # differentiates per lead (never a single swapped-name template).
    if key in ("addressing-price", "offer-discount"):
        opener = f"Hi {first}, still thinking about that piece?"
        detail = f" Totally understand budget matters.{offer_phrase or ' We can find something that fits.'}"
    elif key in ("payment-flexibility", "offer-payment"):
        opener = f"Hi {first}, wanted to follow up about your piece."
        detail = f"{offer_phrase or ' We can split it into smaller payments so it is easier to manage.'}"
    elif key == "flexible-timing":
        opener = f"Hi {first}, no rush at all — just keeping the door open."
        detail = " Whenever the timing feels right, we'll be here."
    elif key == "proof-and-portfolio":
        opener = f"Hi {first}, totally normal to want to see more before booking."
        detail = " Happy to share healed work and answer anything about the process."
    elif key == "low-pressure-consult":
        opener = f"Hi {first}, no pressure either way."
        detail = " If it helps, we can hop on a quick chat to figure out what you want."
    elif key == "rebuild-trust":
        # NEVER a hard sell: no offer phrase, and the past experience is acknowledged
        # without restating its painful details.
        opener = f"Hi {first}, we know your last experience with us wasn't what it should have been."
        detail = (" No pitch — if you ever want to give it another go, your artist would "
                  "work with you directly, your date would be locked in with no "
                  "reschedules on our end, and our manager would be your direct point "
                  "of contact.")
    elif key == "prereq-help":
        # A next-step note, explicitly not a discount pitch (no offer phrase). The real
        # prerequisite stays in the angle basis/prompt; the template never guesses it.
        opener = f"Hi {first}, following up with the honest next step for your piece."
        detail = (" Our artists flagged one thing to sort out first — happy to walk you "
                  "through exactly what it involves and what to do, no booking pressure "
                  "and no sales pitch.")
    elif key == "resume-booking":
        opener = f"Hi {first}, no pressure at all — we can pick up right where we left off."
        detail = (" Everything from your booking is still on file, and the next step is "
                  "the same one we left off on.")
    elif key in ("loyalty-touchup", "offer-loyalty-touchup"):
        # HONEST BY CONSTRUCTION (ju1.3): only reference "your last piece" when a real
        # tattoo_history is on file — otherwise it fabricates a past piece for a lead we
        # have no history for (the anti-fake-personalization guard rejects that draft).
        # With no history, a warm loyalty line grounded only in the recurring segment.
        opener = (
            f"Hi {first}, hope your last piece is healing well." if tattoos
            else f"Hi {first}, it's been a while — we'd love to see you again."
        )
        detail = f" We'd love to have you back.{offer_phrase}"
    elif key == "completion-nudge":
        opener = f"Hi {first}, just a gentle nudge on the booking you started."
        detail = " Happy to finish it whenever you're ready."
    elif key in ("win-back", "offer-win-back"):
        opener = f"Hi {first}, it's been a while and we've been thinking about you."
        detail = f"{offer_phrase}"
    elif key == "past-work" and last_style:
        opener = f"Hi {first}, your last {last_style} piece stuck with me."
        detail = " It's the kind of work we love to see."
    elif key == "shared-craft" and top_interest:
        # Recipient-centered: their interest grounds the copy, but we never assert the
        # SENDER's own affinity/presence from recipient-row data ("we share a soft spot…
        # we spend a lot of our time there") — with a place-bearing interest that became
        # an implied false sender-location claim (same class as bug 2).
        opener = f"Hi {first}, saw you're into {top_interest} and wanted to say hello."
        detail = ""
    elif key == "re-engagement":
        opener = f"Hi {first}, it's been a while and we've been thinking about you."
        detail = ""
    elif key == "csv-note" and notes:
        # B2B intro only for an actual studio lead; a customer gets a plain, warm reach-out.
        opener = (
            f"Hi {first}, reaching out from one studio to another."
            if is_studio else f"Hi {first}, wanted to reach out."
        )
        # The CRM note is staff-written INTERNAL text ("hesitated when we talked
        # price") — it grounds the ANGLE and the operator-facing rationale but is
        # never quoted into the outgoing body (same exposes-internal-wording class
        # as the goal leak, bug 1). Applies to B2B leads too: notes are internal.
        detail = ""
    elif key == "their-positioning":
        # For a person, "came across <full name>" reads like a mail-merge; ground the
        # same research signal as "came across your work" instead.
        opener = (
            f"Hi {first}, came across {name} and wanted to say hello."
            if is_studio else f"Hi {first}, came across your work and wanted to say hello."
        )
        detail = ""
    elif key == "local" and city:
        # A "we're local too" claim is only honest when the SENDER's city is known and
        # actually matches the recipient's — otherwise say hello without claiming to be
        # local (the recipient's own city is fine to mention AS the recipient's).
        # On a genuine match, render OUR canonical city string (_sender) — never the
        # recipient row's raw casing/whitespace as the sender's own identity.
        if is_studio and same_city:
            opener = f"Hi {first}, fellow {_sender} studio here, saying hello."
        elif same_city:
            opener = f"Hi {first}, we're over in {_sender} too — wanted to say hello."
        elif is_studio:
            opener = f"Hi {first}, came across {name} and wanted to say hello."
        else:
            opener = f"Hi {first}, hope {city} is treating you well — wanted to say hello."
        detail = ""
    else:  # generic — honest general intro, no manufactured specifics
        opener = (
            f"Hi {first}, I'm reaching out from one studio to another."
            if is_studio else f"Hi {first}, wanted to reach out and say hello."
        )
        detail = ""

    # The CTA + opt-out line are added by _finalize_outreach_body so there is ONE
    # place that guarantees a clear next step and a resolved (never raw-token) opt-out.
    # COPY-SAFETY: the closing NEVER contains the internal campaign goal (bug 1); the
    # "I run a small studio…" self-intro is peer-studio copy, so customers instead get
    # a warm studio line (the finalizer still appends the real CTA after it).
    if warm_key:
        body = f"{opener}{detail}"
    elif is_studio:
        body = f"{opener}{detail} I run a small studio{city_phrase} and wanted to say hello."
    else:
        body = f"{opener}{detail} We'd love to see you at the studio whenever it suits."
    body = " ".join(body.split())

    subject = None
    if ch in ("gmail", "email"):
        subj_by_key = {
            "addressing-price": f"{first}, about your piece",
            "offer-discount": f"{first}, a little something for your piece",
            "payment-flexibility": f"{first}, an easier way to book",
            "offer-payment": f"{first}, an easier way to book",
            "flexible-timing": f"{first}, whenever you're ready",
            "proof-and-portfolio": f"{first}, a bit more about our work",
            "low-pressure-consult": f"{first}, no-pressure chat?",
            "rebuild-trust": f"{first}, making it right",
            "prereq-help": f"{first}, the next step for your piece",
            "resume-booking": f"{first}, picking up where we left off",
            "loyalty-touchup": f"{first}, come back and see us",
            "offer-loyalty-touchup": f"{first}, come back and see us",
            "completion-nudge": f"{first}, finishing your booking",
            "win-back": f"{first}, it's been too long",
            "offer-win-back": f"{first}, it's been too long",
            "their-positioning": (
                f"Reaching out to {name}" if is_studio else f"{first}, came across your work"
            ),
            "past-work": f"{first}, about your last piece",
            # "kindred … folks" asserted mutual affinity from recipient-row data (and,
            # with a place-bearing interest, implied sender presence) — the subject is
            # recipient-centered for EVERY audience. "Fellow <city> studio" / "one
            # studio to another" are peer-studio subjects — customers get customer-safe
            # ones (65w.13 bug 3; never the recipient's city presented as ours, bug 2).
            "shared-craft": (
                f"{first}, about {top_interest}" if top_interest else f"Hello, {first}"
            ),
            "re-engagement": f"{first}, it's been too long",
            "csv-note": f"A quick hello, {first}",
            "local": (
                # "Fellow <city> studio" only when we genuinely ARE a studio in that
                # city (sender city known + matching) — rendered with OUR canonical
                # city string, never the recipient row's raw one (bug 2).
                f"Fellow {_sender} studio saying hi"
                if (is_studio and same_city)
                else (f"Hello, {first}" if is_studio else f"A hello from the studio, {first}")
            ),
            "generic": (
                f"An intro, one studio to another, {first}" if is_studio
                else f"Hello from the studio, {first}"
            ),
        }
        subject = (subj_by_key.get(key) or f"Hello, {first}").strip()[:60]
    return subject, body


# --------------------------------------------------------------------------- #
# Send-safety finalizer — resolve the opt-out token + guarantee a clear CTA
# --------------------------------------------------------------------------- #
#
# The gated copywriter email cell is REQUIRED to leave the visible {{unsubscribe}}
# token in the body (its validators enforce it) because the growth RFC-8058 sequence
# path fills that token with a one-click URL. The studio outreach path has no such
# URL infrastructure, so a queued studio draft would otherwise carry the literal
# token (and, on the deterministic path, a weak/no CTA) into a real inbox. We resolve
# the token to an honest reply-based opt-out and guarantee a clear next step BEFORE
# the draft is staged. The send path (actions.publish) additionally REFUSES any draft
# that still contains an unresolved placeholder.

_UNSUB_TOKEN = "{{unsubscribe}}"
_PLACEHOLDER_RE = re.compile(r"\{\{[^}]*\}\}")
_OPT_OUT_LINE = (
    "If you'd rather not hear from me, just reply STOP and I won't reach out again."
)
# Phrases that signal the copy already carries a real next step, so we do not bolt a
# second CTA onto copy that already asks for one. Deliberately excludes a bare "reply"
# (the opt-out line uses it) to avoid a false positive.
_CTA_SIGNALS = (
    "reply yes", "reply with", "book a", "book your", "booking link", "send over a booking",
    "schedule", "grab a time", "find a time", "set up a time", "set something up",
    "let me know if you", "hop on a call", "you can grab a time",
)
_OPTOUT_HINTS = (
    "unsubscribe", "opt out", "opt-out", "opt me out", "stop hearing", "no longer",
    "reply stop",
)


def _booking_link() -> str | None:
    """An operator-configured booking link, if one is set. Honest: returns ``None``
    when no real link is configured, so we never fabricate a URL — the CTA falls back
    to a reply-based invite instead."""
    link = (
        os.environ.get("STUDIO_BOOKING_LINK")
        or os.environ.get("SCALERS_BOOKING_LINK")
        or ""
    ).strip()
    return link or None


def _cta_line() -> str:
    """One clear next step for the recipient: the real booking link when configured,
    otherwise an honest reply-based invite that promises nothing we cannot deliver."""
    link = _booking_link()
    if link:
        return f"If you're open to it, you can grab a time here: {link}"
    return "If you're open to it, just reply YES and I'll send over a booking link."


def _has_cta(text: str) -> bool:
    low = text.lower()
    return any(sig in low for sig in _CTA_SIGNALS)


def _looks_like_optout(line: str) -> bool:
    low = line.lower()
    return any(hint in low for hint in _OPTOUT_HINTS)


def _finalize_outreach_body(body: str | None, *, ch: str) -> str | None:
    """Make a staged EMAIL body send-safe and action-oriented BEFORE it is queued.

    1. Resolve the copywriter's visible ``{{unsubscribe}}`` token to a concrete
       reply-based opt-out line (a real recipient opts out by replying) and strip any
       other stray placeholder, so no raw template token reaches a human inbox.
    2. Guarantee a clear CTA: a real booking link when the operator configured one,
       otherwise an honest reply-based invite. A cold email with no next step does not
       convert.

    Deterministic and idempotent. Non-email channels pass through unchanged."""
    if ch not in ("gmail", "email") or not body:
        return body

    # Drop the copywriter's unsubscribe line (any line carrying the token is an opt-out
    # line by contract — the cell is told to END with it). An inline token (no dedicated
    # line) is stripped but its surrounding body text is kept, so no content is lost.
    kept: list[str] = []
    for line in body.strip().splitlines():
        if _UNSUB_TOKEN in line:
            remainder = line.replace(_UNSUB_TOKEN, "").strip()
            if remainder and not _looks_like_optout(remainder):
                kept.append(remainder)
            continue
        kept.append(line)
    core = _PLACEHOLDER_RE.sub("", "\n".join(kept)).strip()

    if not _has_cta(core):
        core = f"{core}\n\n{_cta_line()}"
    return f"{core}\n\n{_OPT_OUT_LINE}"


def build_outreach_draft(
    facts: dict[str, Any],
    *,
    goal: str = "",
    channel: str | None = None,
    plan_channels: list[str] | None = None,
    tenant_id: str | None = None,
    deep_research: bool | None = None,
    research_depth: str | None = None,
    research: list[dict[str, Any]] | None = None,
    profile: Any = None,
    offer: Any = None,
    sender_city: str | None = None,
    artist_voice: str | None = None,
) -> dict[str, Any]:
    """Build ONE personalized outreach draft for a lead — REAL copywriter-written,
    brand-voiced, and grounded only in facts the system can substantiate.

    ``artist_voice`` is the SIGN-OFF IDENTITY GATE: the artist name the copy may
    sign as / speak for — pass it ONLY when the operator explicitly set the campaign
    artist or this lead's own record carries that artist affinity. ``None`` (the
    default) hard-forbids signing as ANY individual artist: the copy signs as the
    studio, so a draft can never fabricate an artist relationship the data lacks.

    ``profile`` (a grounded :class:`~studio.psych_profile.PsychProfile`) and ``offer`` (a
    REAL substantiated :class:`~studio.offers.Offer`, or None) drive the category/objection
    branching: a price/timing/trust objection leads the angle, and a discount is mentioned
    ONLY when ``offer`` is a real substantiated offer — never invented.

    The copy is produced by the gated **copywriter email cell** (``cells.copywriter``)
    in the SENDER's resolved **brand voice** (``resolve_brand_voice``), from a prompt
    that may use ONLY the grounding bundle (recipient name / city / CSV note / any
    first-party persona rows / cite-only verified research) and is explicitly
    forbidden from asserting any unknown specific about the real recipient studio.
    With no Anthropic key it degrades to a deterministic honest draft; with no
    Firecrawl key (or research not opted in — see :func:`_research_enabled`; either
    ``deep_research=True`` or the interview's ``research_depth="deep"`` enables it)
    the bundle simply carries no research — never a fabricated fact.

    Sync-callable (runs inside ``asyncio.to_thread`` today); the cell's ``run_sync``
    bridges to the async agent internally. Returns the SAME contract
    ``{channel, target, subject, draft, grounding, customer_id}`` where ``grounding``
    is the audit list of exactly which facts/sources the copy was allowed to use, so
    the caller can stage it as a PENDING action (HELD). Nothing is sent here."""
    name = (facts.get("name") or "there").strip()
    traits = facts.get("persona_traits", {}) or {}
    interests = facts.get("interests", []) or []
    aesthetic = traits.get("aesthetic_lean")
    top_interest = aesthetic or (interests[0] if interests else None)
    city = facts.get("city")
    notes = (facts.get("notes") or "").strip()
    lifecycle = traits.get("lifecycle_stage")
    win_back = bool(traits.get("win_back_candidate"))
    tattoos = facts.get("tattoo_history", []) or []
    last_style = tattoos[0]["style"] if tattoos else None

    # Channel: respect the explicit choice / consent rules. A lead reachable only by
    # email (real public address, opted in, no IG handle) routes to email rather than
    # defaulting to an instagram DM it has no handle for.
    if channel:
        ch = channel.strip().lower()
    else:
        ch = choose_channel(facts, plan_channels)
        if not ch:
            # Operator-explicit channels exhausted without consent (e.g. an 'sms'
            # child run against a lead with no SMS opt-in): an honest skip — the
            # caller counts it. Never silently diverted to another channel.
            return {
                "skip_reason": (
                    f"requested channel(s) {', '.join(plan_channels or [])} need a "
                    "consent this lead has not given — not drafted"
                ),
                "channel": "",
                "target": None,
                "lead": facts.get("name"),
            }
        # Only when no channel was explicitly requested: a lead reachable solely by
        # email (real address, opted in, no IG handle) should route to email rather
        # than fall through to an instagram DM it has no handle for.
        if ch == "instagram" and not plan_channels and not facts.get("ig_handle") \
                and facts.get("email") and facts.get("email_opt_in"):
            ch = "gmail"
    if ch == "email":
        ch = "gmail"

    # SMS OPT-OUT GUARD: an explicit sms_opt_in=False on the customer row (a real
    # reply-STOP / withheld consent) suppresses SMS even when the caller requested it —
    # the draft downgrades to email (or organic IG) and the brief/grounding carries the
    # note so no strategy output downstream proposes texting this lead. An ABSENT flag
    # (None) is not an opt-out and changes nothing.
    sms_suppressed = facts.get("sms_opt_in") is False
    if sms_suppressed and ch == "sms":
        ch = "gmail" if (facts.get("email") and facts.get("email_opt_in")) else "instagram"

    # --- grounding audit: exactly the facts the copy is allowed to use ----------- #
    grounding: list[str] = [f"name={name}"]
    if sms_suppressed:
        grounding.append("channel_guard=SMS suppressed — email only")
    if city:
        grounding.append(f"city={city}")
    if notes:
        grounding.append(f"note={notes}")
    if top_interest:
        grounding.append(f"interest/aesthetic={top_interest}")
    if lifecycle:
        grounding.append(f"lifecycle={lifecycle}")
    if last_style:
        grounding.append(f"last_tattoo_style={last_style}")
    if win_back:
        grounding.append("win_back_candidate=true")
    # Evidence-cited public-web enrichment (operator-initiated, stored as a customer
    # memory): its source URLs join the grounding audit so the operator can verify
    # exactly which public evidence this draft was allowed to lean on.
    try:
        from studio.lead_enrichment import enrichment_memory

        _enr = enrichment_memory(facts)
    except Exception:
        _enr = None
    if _enr is not None:
        for _u in (_enr.get("metadata") or {}).get("urls", [])[:5]:
            grounding.append(f"enrichment:{_u}")

    # --- per-lead angle: the DISTINCT basis this draft leads with (real-only) ----- #
    # Resolve research up front so the angle can prefer this lead's verified public
    # positioning (its strongest differentiator) when one exists.
    if research is None and ch in ("gmail", "email") and _llm_copy_enabled():
        research = research_studio(
            facts, enabled=_research_enabled(deep_research, research_depth)
        )
    angle = _choose_angle(facts, research, profile=profile, offer=offer)
    why_different = _angle_rationale(angle, facts, research, name)
    grounding.append(f"angle={angle['key']}")
    # Record the grounded objection + any REAL substantiated offer the angle stands on,
    # so the evidence panel can show WHY this lead is being re-engaged this way. The offer
    # is only ever the real code/terms (build passes the substantiated Offer, or None).
    if profile is not None:
        obj_val, obj_sig, _ = _pf(profile, "primary_objection")
        if obj_val and obj_val != "none-found" and obj_sig in ("stated", "inferred"):
            grounding.append(f"objection={obj_val}")
        cat_val, _, _ = _pf(profile, "umbrella_category")
        if cat_val:
            grounding.append(f"category={cat_val}")
    if offer is not None:
        grounding.append(f"offer={offer.code}")
    if angle["generic"]:
        grounding.append("personalization=generic-honest")
    elif angle["inferred"]:
        grounding.append("personalization=inferred")
    else:
        grounding.append("personalization=grounded")
    # Auditable sign-off identity: which sender identity the copy was ALLOWED to use.
    grounding.append(f"sign_off=artist:{artist_voice}" if artist_voice else "sign_off=studio")

    subject: str | None = None
    body: str | None = None
    # The REAL model the copy was written with — captured from the actual cell so the
    # caller records a TRUTHFUL agent_run.model (never a hardcoded literal that could
    # silently drift from the cell's pin). None until a path sets it.
    copy_model: str | None = None

    # --- REAL copywriter path (gated email cell, brand voice, verified research) -- #
    if ch in ("gmail", "email") and _llm_copy_enabled():
        try:
            brand_voice_context, approved_claims = resolve_brand_voice(tenant_id)
            # Research already resolved above for the angle; only fetch if still unset.
            if research is None:
                research = research_studio(
                    facts, enabled=_research_enabled(deep_research, research_depth)
                )
            from cells.copywriter import build_copywriter_email_cell

            cell = build_copywriter_email_cell(
                brand_voice_context=brand_voice_context,
                approved_claims=approved_claims,
            )
            # Truthful model provenance: read the id off the cell that actually ran.
            _cm = getattr(cell, "model", None)
            copy_model = _cm if isinstance(_cm, str) else str(_cm)
            copy = cell.run_sync(
                _build_email_prompt(facts, goal=goal, research=research, angle=angle,
                                    offer=offer, profile=profile,
                                    artist_voice=artist_voice)
            )
            subject, body = copy.subject, copy.body
            if brand_voice_context:
                # Record the tenant the voice was ACTUALLY resolved for. brand_voice_context
                # is only non-empty when a real pack resolved, so (tenant_id or the resolved
                # default) is guaranteed non-None here — never a fixture stand-in for a real
                # tenant that passed its own id.
                grounding.append(f"brand_voice={tenant_id or _default_tenant()}")
            for r in research:
                grounding.append(f"research:{r['url']}")
            grounding.append("copy=copywriter_email_cell")
        except Exception as exc:  # any cell/network failure -> honest deterministic
            subject = body = None
            copy_model = None  # the cell did not produce the copy; not its model
            grounding.append(f"copy=deterministic_fallback({type(exc).__name__})")

    # --- deterministic fallback (no key / non-email channel / cell failed) ------- #
    if body is None:
        subject, body = _template_outreach(
            facts, goal=goal, ch=ch, angle=angle, offer=offer,
            sender_city=sender_city if sender_city is not None else _resolve_sender_city(tenant_id),
        )
        if not any(g.startswith("copy=") for g in grounding):
            grounding.append("copy=deterministic_template")
        # Truthful provenance: a template wrote this, not a model.
        copy_model = copy_model or "deterministic_template"

    # Resolve the opt-out token + guarantee a clear CTA BEFORE staging, so a queued
    # draft never carries a raw {{unsubscribe}} token (or a weak/no CTA) into a real
    # inbox. The send path (actions.publish) refuses any unresolved placeholder too.
    body = _finalize_outreach_body(body, ch=ch)
    if ch in ("gmail", "email"):
        grounding.append("cta=booking-link" if _booking_link() else "cta=reply-based")
        grounding.append("opt_out=reply-based")

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
        # The REAL model that wrote this copy (a cell pin, or a deterministic marker) —
        # the caller records it verbatim as the draft agent_run.model (no literal).
        "copy_model": copy_model,
        # Per-lead personalization proof: the distinct angle this draft leads with, the
        # honest "why it differs from the others" rationale, and whether it is honestly
        # generic (thin data) vs grounded. Surfaced so the operator can SEE it is real.
        "angle": angle["label"],
        "angle_key": angle["key"],
        "why_different": why_different,
        "generic": angle["generic"],
        "inferred": angle["inferred"],
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
    # Accept the common SINGULAR header too ("interest") — a real uploaded CSV often uses
    # it, and dropping that column left EVERY lead with no interest, collapsing the whole
    # cohort onto the generic angle so drafts came out identical (the operator's "same
    # generic result"). Reading the real column grounds each draft in the lead's own,
    # distinct craft interest — real evidence from their CSV, never a fabricated one.
    interests_raw = row.get("interests") or row.get("interest") or ""
    interests = [s.strip() for s in interests_raw.replace(",", ";").split(";") if s.strip()]
    linkedin = (row.get("linkedin") or "").strip() or None
    # Extended, adapter-normalized fields (ADR §4.6) — persisted so the ``notes`` angle
    # and the category/objection branching read real values, not a dropped column.
    ext = {c: ((row.get(c) or "").strip() or None) for c in _LEAD_EXT_COLUMNS}

    ensure_lead_columns(dsn)
    with _connect(dsn) as conn:
        existing = None
        if email:
            existing = conn.execute(
                "SELECT id FROM customers WHERE tenant_id = %s AND lower(email) = lower(%s) LIMIT 1",
                (tenant_id, email),
            ).fetchone()
        if existing is not None:
            # Backfill only NULL/empty columns; never clobber seeded ground truth. The
            # extended columns backfill the same way (COALESCE) so an uploaded note/artist
            # fills a gap but never overwrites a real seeded value.
            conn.execute(
                """
                UPDATE customers SET
                    name = COALESCE(NULLIF(name, ''), %s),
                    city = COALESCE(city, %s),
                    state = COALESCE(state, %s),
                    linkedin_handle = COALESCE(linkedin_handle, %s),
                    interests = CASE WHEN interests IS NULL OR cardinality(interests) = 0
                                     THEN %s ELSE interests END,
                    notes = COALESCE(notes, %s),
                    artist = COALESCE(artist, %s),
                    shop = COALESCE(shop, %s),
                    lead_stage = COALESCE(lead_stage, %s),
                    customer_type = COALESCE(customer_type, %s),
                    payment_status = COALESCE(payment_status, %s)
                WHERE id = %s
                """,
                (name, city, state, linkedin, interests,
                 ext["notes"], ext["artist"], ext["shop"], ext["lead_stage"],
                 ext["customer_type"], ext["payment_status"], existing["id"]),
            )
            return {"customer_id": existing["id"], "created": False}

        cust_id = "cust_" + uuid.uuid4().hex[:16]
        conn.execute(
            """
            INSERT INTO customers
                (id, tenant_id, name, email, linkedin_handle, city, state,
                 interests, preferred_channels, email_opt_in, sms_opt_in, source,
                 notes, artist, shop, lead_stage, customer_type, payment_status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s)
            """,
            (
                cust_id, tenant_id, name, email, linkedin, city, state,
                interests, [], bool(email), False, "studio_upload",
                ext["notes"], ext["artist"], ext["shop"], ext["lead_stage"],
                ext["customer_type"], ext["payment_status"],
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
