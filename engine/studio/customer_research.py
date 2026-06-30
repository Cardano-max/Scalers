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


# --------------------------------------------------------------------------- #
# Brand voice + verified research — the REAL inputs the copywriter cell consumes
# --------------------------------------------------------------------------- #

# The sending tenant (whose brand voice every outreach draft is written in). The
# studio Host is single-tenant today; resolved once here so the call-site
# (studio.agui) does not have to thread it through.
_DEFAULT_TENANT = os.environ.get("SCALERS_TENANT_ID") or "ladies8391"


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _llm_copy_enabled() -> bool:
    """Whether to write copy with the REAL copywriter cell (vs the deterministic
    fallback). Honors an explicit ``SCALERS_OUTREACH_LLM`` override; otherwise auto:
    on iff an Anthropic key is present (no key -> honest deterministic copy)."""
    override = os.environ.get("SCALERS_OUTREACH_LLM")
    if override is not None:
        return override.strip().lower() in ("1", "true", "yes", "on")
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _research_enabled(deep_research: bool | None) -> bool:
    """Deep per-studio web research (Firecrawl) is OFF by default — it makes live
    egress and is gated behind an explicit opt-in (``deep_research=True`` or the
    ``STUDIO_DEEP_RESEARCH`` env flag). With no Firecrawl key wired it degrades to
    honest-empty regardless."""
    if deep_research is not None:
        return bool(deep_research)
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
    Degrades honestly to ``("", ())`` if the pack / dimensions cannot be resolved —
    the copy then writes from goal + grounded recipient facts only, never a fabricated
    voice."""
    tid = tenant_id or _DEFAULT_TENANT
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
    a source. A failure degrades to ``[]`` (no citation), never an invented fact."""
    if not enabled:
        return []
    name = (facts.get("name") or "").strip()
    if not name:
        return []
    city = (facts.get("city") or "").strip()
    cust_id = facts.get("customer_id")
    try:
        from research.pipeline import live_registry

        provider = live_registry().get("firecrawl")
        if provider is None or not getattr(provider, "enabled", False):
            return []
        query = " ".join(x for x in [name, city, "tattoo studio"] if x)
        # Collect more than we keep so we can diversify across source types.
        raw: list[dict[str, Any]] = []
        seen: set[str] = set()
        for hit in provider.search(query, limit=6):
            url = getattr(hit, "url", None)
            if not url or url in seen:
                continue
            seen.add(url)
            raw.append({
                "title": getattr(hit, "title", None),
                "snippet": getattr(hit, "snippet", None),
                "url": url,
                "source_type": _classify_source(url),
                "customer_id": cust_id,
            })
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


def _choose_angle(
    facts: dict[str, Any], research: list[dict[str, Any]] | None
) -> dict[str, Any]:
    """Pick ONE distinct outreach angle for this lead from REAL differentiators only.

    Ranked most-lead-specific first: verified research positioning -> real past-work
    from our own history -> shared craft (CSV interest, else inferred persona lean) ->
    re-engagement (lifecycle / win-back persona signal) -> the CSV note -> light local
    (city) -> honest-generic. Returns ``{key, label, basis, inferred, generic}`` where
    ``basis`` is the verbatim real fact the angle stands on, ``inferred`` flags a
    persona-derived (not hard) signal, and ``generic`` is True ONLY when the lead has
    NO distinguishing data — in which case we say so honestly rather than fake
    personalization. NEVER invents a differentiator."""
    name = (facts.get("name") or "this studio").strip()
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


def _angle_rationale(angle: dict[str, Any], name: str) -> str:
    """One honest sentence: why THIS draft differs from the others — the distinct angle
    chosen for this lead and the real basis it stands on. A generic lead is labeled
    honestly (not dressed up as personalized)."""
    who = (name or "this lead").strip()
    if angle["generic"]:
        return (
            f"Honest-generic: no distinguishing research or history on file for {who}, "
            "so this draft stays a general introduction rather than faking personalization."
        )
    qualifier = " (inferred from persona, not a hard fact)" if angle["inferred"] else ""
    return f"Personalized on {angle['label']}{qualifier}; grounded on {angle['basis']}."


def _build_email_prompt(
    facts: dict[str, Any], *, goal: str, research: list[dict[str, Any]],
    angle: dict[str, Any],
) -> str:
    """Assemble the copywriter run prompt. It exposes the lead's REAL grounded facts
    (name / city / CSV note / first-party interests + past work from our records /
    cite-only research), threads in the DISTINCT per-lead angle to lead with, and
    hard-forbids asserting anything the system cannot substantiate about a REAL
    business. Persona-inferred signals are passed as clearly-marked soft impressions,
    never as hard facts."""
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

    goal_line = (goal or "open a genuine conversation").strip()

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

    return "\n".join([
        "You are writing ONE short, warm cold-outreach EMAIL, in the BRAND VOICE "
        "above, to a REAL tattoo studio (the recipient). Treat this as a genuine first "
        "introduction (purpose = intro), and make it UNMISTAKABLY for this specific "
        "recipient — not a template with the name swapped.",
        "",
        "# WHAT YOU ACTUALLY KNOW ABOUT THE RECIPIENT",
        "# (hard facts you may state about them):",
        *known,
        *inferred_block,
        "",
        "# RESEARCH (verbatim web snippets about the recipient — cite-only context):",
        research_lines,
        "",
        *angle_block,
        "",
        "# HARD GROUNDING RULES — no fabrication about a REAL business:",
        "- You may reference ONLY the hard facts above, the SOFT signals (marked as "
        "impressions, never as fact), and a research snippet ONLY when it is "
        "unmistakably about THIS studio and you state nothing beyond what it literally says.",
        "- Do NOT invent or imply anything NOT listed above about the recipient's "
        "style, artists, awards, reputation, clientele, or history. If a specific is "
        "missing, stay general and honest rather than guessing.",
        "- Everything you say about YOURSELF (the sender) must come from the brand "
        "voice's approved claims above — nothing else.",
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


def _template_outreach(
    facts: dict[str, Any], *, goal: str, ch: str, angle: dict[str, Any],
) -> tuple[str | None, str]:
    """Deterministic fallback copy (no model): honest, grounded only in real facts,
    and SHAPED BY THE PER-LEAD ANGLE so two leads do not collapse to one template.

    Used when LLM copy is disabled (no Anthropic key) or the cell fails. Still never
    invents a recipient detail — opener, detail, and subject are keyed off the angle
    chosen from this lead's real differentiators (and honestly generic when thin)."""
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

    city_phrase = f" over in {city}" if city else ""
    goal_line = (goal or "open a genuine conversation").strip()
    key = angle["key"]

    # Opener + detail are keyed off the distinct angle so the deterministic path also
    # differentiates per lead (never a single swapped-name template).
    if key == "past-work" and last_style:
        opener = f"Hi {first}, your last {last_style} piece stuck with me."
        detail = " It's the kind of work we love to see."
    elif key == "shared-craft" and top_interest:
        opener = f"Hi {first}, looks like we share a soft spot for {top_interest}."
        detail = " We spend a lot of our time there too."
    elif key == "re-engagement":
        opener = f"Hi {first}, it's been a while and we've been thinking about you."
        detail = ""
    elif key == "csv-note" and notes:
        opener = f"Hi {first}, reaching out from one studio to another."
        detail = f" Saw on our end you're {notes.lower()}."
    elif key == "their-positioning":
        opener = f"Hi {first}, came across {name} and wanted to say hello."
        detail = ""
    elif key == "local" and city:
        opener = f"Hi {first}, fellow {city} studio here, saying hello."
        detail = ""
    else:  # generic — honest general intro, no manufactured specifics
        opener = f"Hi {first}, I'm reaching out from one studio to another."
        detail = ""

    # The CTA + opt-out line are added by _finalize_outreach_body so there is ONE
    # place that guarantees a clear next step and a resolved (never raw-token) opt-out.
    body = (
        f"{opener}{detail} I run a small studio{city_phrase} and wanted to say hello "
        f"and {goal_line}."
    )
    body = " ".join(body.split())

    subject = None
    if ch in ("gmail", "email"):
        subj_by_key = {
            "their-positioning": f"Reaching out to {name}",
            "past-work": f"{first}, about your last piece",
            "shared-craft": f"{first}, kindred {top_interest or 'studio'} folks",
            "re-engagement": f"{first}, it's been too long",
            "csv-note": f"A quick hello, {first}",
            "local": f"Fellow {city} studio saying hi" if city else f"Hello, {first}",
            "generic": f"An intro, one studio to another — {first}",
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
    research: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build ONE personalized outreach draft for a lead — REAL copywriter-written,
    brand-voiced, and grounded only in facts the system can substantiate.

    The copy is produced by the gated **copywriter email cell** (``cells.copywriter``)
    in the SENDER's resolved **brand voice** (``resolve_brand_voice``), from a prompt
    that may use ONLY the grounding bundle (recipient name / city / CSV note / any
    first-party persona rows / cite-only verified research) and is explicitly
    forbidden from asserting any unknown specific about the real recipient studio.
    With no Anthropic key it degrades to a deterministic honest draft; with no
    Firecrawl key (or ``deep_research`` off) the bundle simply carries no research —
    never a fabricated fact.

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
        # Only when no channel was explicitly requested: a lead reachable solely by
        # email (real address, opted in, no IG handle) should route to email rather
        # than fall through to an instagram DM it has no handle for.
        if ch == "instagram" and not plan_channels and not facts.get("ig_handle") \
                and facts.get("email") and facts.get("email_opt_in"):
            ch = "gmail"
    if ch == "email":
        ch = "gmail"

    # --- grounding audit: exactly the facts the copy is allowed to use ----------- #
    grounding: list[str] = [f"name={name}"]
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

    # --- per-lead angle: the DISTINCT basis this draft leads with (real-only) ----- #
    # Resolve research up front so the angle can prefer this lead's verified public
    # positioning (its strongest differentiator) when one exists.
    if research is None and ch in ("gmail", "email") and _llm_copy_enabled():
        research = research_studio(facts, enabled=_research_enabled(deep_research))
    angle = _choose_angle(facts, research)
    why_different = _angle_rationale(angle, name)
    grounding.append(f"angle={angle['key']}")
    if angle["generic"]:
        grounding.append("personalization=generic-honest")
    elif angle["inferred"]:
        grounding.append("personalization=inferred")
    else:
        grounding.append("personalization=grounded")

    subject: str | None = None
    body: str | None = None

    # --- REAL copywriter path (gated email cell, brand voice, verified research) -- #
    if ch in ("gmail", "email") and _llm_copy_enabled():
        try:
            brand_voice_context, approved_claims = resolve_brand_voice(tenant_id)
            # Research already resolved above for the angle; only fetch if still unset.
            if research is None:
                research = research_studio(facts, enabled=_research_enabled(deep_research))
            from cells.copywriter import build_copywriter_email_cell

            cell = build_copywriter_email_cell(
                brand_voice_context=brand_voice_context,
                approved_claims=approved_claims,
            )
            copy = cell.run_sync(
                _build_email_prompt(facts, goal=goal, research=research, angle=angle)
            )
            subject, body = copy.subject, copy.body
            if brand_voice_context:
                grounding.append(f"brand_voice={tenant_id or _DEFAULT_TENANT}")
            for r in research:
                grounding.append(f"research:{r['url']}")
            grounding.append("copy=copywriter_email_cell")
        except Exception as exc:  # any cell/network failure -> honest deterministic
            subject = body = None
            grounding.append(f"copy=deterministic_fallback({type(exc).__name__})")

    # --- deterministic fallback (no key / non-email channel / cell failed) ------- #
    if body is None:
        subject, body = _template_outreach(facts, goal=goal, ch=ch, angle=angle)
        if not any(g.startswith("copy=") for g in grounding):
            grounding.append("copy=deterministic_template")

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
