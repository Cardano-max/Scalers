"""Evidence-cited lead ENRICHMENT — public-web personalization facts, cited or nothing.

Given ONE customer, run 1–3 CITED public-web lookups through the SAME research seam
the Instagram pipeline uses (:func:`studio.ig_pipeline.run_trend_research` → the
vetted ``research.pipeline.live_registry`` Firecrawl provider) and merge the
verifiable, useful evidence into that customer's memory (``subject_type='customer'``)
so the strategist/copywriter grounds personalization in REAL public facts — their
business, their public creative interests — instead of guesses.

CITATION DISCIPLINE (copied from ``run_trend_research`` / ``research_studio``):
  * Every stored fact is a VERBATIM provider hit (title/snippet) and carries its
    source URL — a fact without a URL or without verbatim text is REJECTED by the
    storage path (:func:`citable_facts`), never kept.
  * A keyless / failed / empty search is an honest MISS: the query lands in
    ``misses`` with the provider's concrete note, and nothing is invented.
  * NO login-walled scraping: only the PUBLIC search snippet is ever used (the seam
    returns search results, it never logs in). A hit on a login-walled host
    (LinkedIn/Instagram/Facebook profile pages) is stored as a URL POINTER plus
    whatever the public snippet said, flagged ``login_walled=True`` — never an
    invented detail from behind the wall.

ETHICS BOUNDARY (non-negotiable — see :func:`suppress_sensitive`): the enrichment
NEVER infers or stores sensitive traits — age, gender, ethnicity, religion, health,
sexual orientation, political views, financial distress — whether a page asserts
them or not. It records only what a person has publicly and professionally
published about themselves (job, business, public creative interests, public
handles). Suppressions are counted (``{"suppressed": n}``) so the scrub is visible.

WRITE SEMANTICS: only when at least one cited fact survives is ONE customer memory
written (metadata ``source='public-web-enrichment'``); re-enriching REPLACES the
previous enrichment memory (write-new-then-delete-stale on the same natural key),
never stacking duplicates. Zero facts → zero writes, honest miss returned.

Operator-initiated ONLY (``POST /studio/customers/{id}/enrich``) — never auto-run
in any loop; per-lead live egress stays a deliberate human decision.
"""

from __future__ import annotations

import os
from typing import Any

from research.protected_traits import (
    AGE,
    ETHNICITY,
    FINANCIAL_STATUS,
    GENDER,
    HEALTH,
    IMMIGRATION_STATUS,
    POLITICAL_VIEWS,
    RELIGION,
    SEXUALITY,
    trait_violations,
)
from studio.ig_pipeline import run_trend_research

_DEFAULT_DSN = "postgresql://scalers:scalers@localhost:5432/scalers"

# The metadata tag that IS the enrichment memory's natural key per customer:
# exactly one memory per (tenant, customer) carries it at any time.
MEMORY_SOURCE = "public-web-enrichment"

# The sensitive-trait categories the post-filter suppresses on. These are the
# project-wide protected categories from research.protected_traits — reused (not
# redefined) so the enrichment ban and the psych/research bans can never drift.
SENSITIVE_KEYS: tuple[str, ...] = (
    AGE, GENDER, ETHNICITY, RELIGION, HEALTH, SEXUALITY,
    FINANCIAL_STATUS, IMMIGRATION_STATUS, POLITICAL_VIEWS,
)

# Consumer mailbox domains: an address there says nothing professional about the
# person, so it never shapes a query. A BUSINESS domain, by contrast, is public
# professional info the person chose to write from — fair to search.
_FREEMAIL_DOMAINS: frozenset[str] = frozenset({
    "gmail.com", "googlemail.com", "yahoo.com", "ymail.com", "hotmail.com",
    "outlook.com", "live.com", "msn.com", "aol.com", "icloud.com", "me.com",
    "mac.com", "proton.me", "protonmail.com", "pm.me", "fastmail.com",
    "fastmail.fm", "gmx.com", "gmx.net", "mail.com", "yandex.com", "yandex.ru",
    "zoho.com", "hey.com", "comcast.net", "att.net", "verizon.net", "cox.net",
})

# Hosts whose full profiles sit behind a login (LinkedIn especially): the public
# search snippet is all we may use — the stored fact is a URL pointer + snippet.
_LOGIN_WALLED_HOSTS: tuple[str, ...] = (
    "linkedin.", "instagram.", "facebook.", "x.com", "twitter.", "tiktok.",
    "threads.",
)


def _dsn(dsn: str | None = None) -> str:
    return dsn or os.environ.get("ENGINE_DATABASE_URL") or _DEFAULT_DSN


def business_email_domain(email: str | None) -> str | None:
    """The lead's BUSINESS email domain, or honest ``None``.

    A freemail address (gmail/yahoo/...) reveals nothing professional and returns
    None; so do test/reserved domains (``.invalid``/``.test``/``.local``/
    ``example.*``). A surviving domain is itself public professional info — the
    person publishes it every time they write from it."""
    addr = (email or "").strip().lower()
    if "@" not in addr:
        return None
    domain = addr.rsplit("@", 1)[-1].strip().strip(".")
    if not domain or "." not in domain:
        return None
    if domain in _FREEMAIL_DOMAINS:
        return None
    tld = domain.rsplit(".", 1)[-1]
    if tld in ("invalid", "test", "local", "localhost", "example"):
        return None
    if domain == "example" or domain.startswith("example."):
        return None
    return domain


def build_enrichment_queries(facts: dict[str, Any]) -> list[tuple[str, str]]:
    """The 1–3 ``(angle, query)`` pairs for this lead, shaped ONLY by fields the
    lead actually has (absent fields drop a query — never a padded guess):

      1. ``public-presence`` — ``"{name}" {city}``: who this person publicly is.
      2. ``business-domain`` — ``"{name}" "{domain}"``: the person tied to their
         own business domain (only for a non-freemail address).
      3. ``professional-interest`` — ``"{name}" {city} {first CSV interest}``:
         their public creative/professional context (only when an interest is on
         file).

    The name is always quoted so the search stays about THIS person. A nameless
    customer yields ``[]`` — there is nothing honest to search."""
    name = (facts.get("name") or "").strip()
    if not name:
        return []
    city = (facts.get("city") or "").strip()
    queries: list[tuple[str, str]] = [
        ("public-presence", " ".join(x for x in (f'"{name}"', city) if x)),
    ]
    domain = business_email_domain(facts.get("email"))
    if domain:
        queries.append(("business-domain", f'"{name}" "{domain}"'))
    interest = next(
        (str(i).strip() for i in (facts.get("interests") or []) if str(i).strip()), ""
    )
    if interest:
        queries.append(
            ("professional-interest",
             " ".join(x for x in (f'"{name}"', city, interest) if x))
        )
    return queries[:3]


def _login_walled(url: str) -> bool:
    host = url.split("//", 1)[-1].split("/", 1)[0].lower()
    return any(h in host for h in _LOGIN_WALLED_HOSTS)


def citable_facts(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The facts that pass the CITATION GATE: a non-empty source ``url`` AND a
    non-empty verbatim ``quote``. Anything else is rejected — a claim the system
    cannot point at is not evidence, it is fabrication. Pure."""
    return [
        f for f in facts
        if (f.get("url") or "").strip() and (f.get("quote") or "").strip()
    ]


def suppress_sensitive(
    facts: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Drop every fact whose text asserts a SENSITIVE personal trait
    (:data:`SENSITIVE_KEYS` — age/gender/ethnicity/religion/health/orientation/
    politics/financial/immigration vocabulary). Returns ``(kept, suppressed_n)``.

    WHY THIS EXISTS: enrichment reads the public web about a real person. Even
    when a third-party page asserts someone's demographics, storing that assertion
    would turn the marketing system into a protected-characteristics profiler —
    banned outright here (spec §7/§24). Personalization may use what a person has
    publicly and PROFESSIONALLY published about themselves (their business, their
    public creative work), never who they are demographically. The filter is
    fail-closed with NO carve-outs (unlike the psych path's first-party
    exemptions): third-party web text about a person cannot self-exempt, and an
    over-suppressed fact merely costs a citation while an under-suppressed one
    asserts a protected trait. Suppressions are COUNTED (the caller reports
    ``{"suppressed": n}``) so the scrub is visible, but the asserted content is
    never echoed back — repeating the inference would be storing it."""
    kept: list[dict[str, Any]] = []
    suppressed = 0
    for fact in facts:
        text = f"{fact.get('quote') or ''}\n{fact.get('title') or ''}"
        viols = trait_violations(text, allowed=frozenset(), first_party_corpus="")
        if any(v.category in SENSITIVE_KEYS for v in viols):
            suppressed += 1
            continue
        kept.append(fact)
    return kept, suppressed


def _collect_cited_facts(
    queries: list[tuple[str, str]], *, limit: int = 5,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Run each query through the shared research seam and return
    ``(found, misses)``. Every found fact is a verbatim provider hit —
    ``{quote, url, query, angle, source_type, login_walled}`` — URL-deduped across
    queries and already citation-gated. A query with nothing citable lands in
    ``misses`` with the seam's concrete note (honest, never padded)."""
    from studio.customer_research import _classify_source

    found: list[dict[str, Any]] = []
    misses: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for angle, query in queries:
        res = run_trend_research(query, limit=limit)
        hits: list[dict[str, Any]] = []
        for s in res.get("sources") or []:
            url = str(s.get("url") or "").strip()
            quote = " — ".join(
                x for x in (
                    str(s.get("title") or "").strip(),
                    str(s.get("snippet") or "").strip(),
                ) if x
            )
            if not url or not quote:
                continue  # citation gate: no URL / no verbatim text -> not a fact
            if url in seen_urls:
                continue
            seen_urls.add(url)
            hits.append({
                "quote": quote,
                "url": url,
                "query": query,
                "angle": angle,
                "source_type": _classify_source(url),
                "login_walled": _login_walled(url),
            })
        if hits:
            found.extend(hits)
        else:
            misses.append({
                "query": query,
                "note": res.get("note") or "search ran but returned no usable sources",
            })
    return found, misses


def _write_enrichment_memory(
    tenant_id: str, facts: dict[str, Any], kept: list[dict[str, Any]],
    *, dsn: str | None = None,
) -> str | None:
    """Write THE one enrichment memory for this customer and return its id.

    STORAGE-PATH CITATION GATE (defense in depth): :func:`citable_facts` re-runs
    here, so a fact without a URL can never be persisted even if a caller slips
    one past collection; nothing citable left → ``None``, no write. The memory
    text carries each fact's URL INLINE; metadata carries ``source``/``urls``/
    ``queries`` and ``enriched_at`` read from the DB's own ``now()``.

    REPLACE, NEVER STACK: the new row is upserted FIRST, then every OTHER memory
    for this customer tagged ``source='public-web-enrichment'`` is deleted — so a
    failed write leaves the previous enrichment intact, and a successful re-run
    leaves exactly one."""
    citable = citable_facts(kept)
    if not citable:
        return None
    import psycopg

    from memory import MemoryStore

    cust_id = facts["customer_id"]
    name = (facts.get("name") or "").strip() or cust_id
    lines = [
        f"Public-web enrichment for {name} — verifiable public facts, each verbatim "
        "from a public search snippet with its source URL (verify at the URL; use a "
        "fact only when it is unmistakably about this person):"
    ]
    for f in citable:
        pointer = (
            " [login-walled profile — URL pointer + public snippet only]"
            if f.get("login_walled") else ""
        )
        lines.append(f'- "{f["quote"]}" ({f["url"]}){pointer}')
    text = "\n".join(lines)

    resolved = _dsn(dsn)
    with psycopg.connect(resolved, autocommit=True) as conn:
        enriched_at = conn.execute("SELECT now()").fetchone()[0]
    store = MemoryStore(dsn=resolved)
    store.ensure_schema()
    mem_id = store.write(
        tenant_id=tenant_id,
        subject_type="customer",
        subject_id=cust_id,
        text=text,
        metadata={
            "kind": "enrichment",
            "source": MEMORY_SOURCE,
            "urls": [f["url"] for f in citable],
            "queries": sorted({f["query"] for f in citable}),
            "enriched_at": enriched_at.isoformat(),
        },
    )
    with psycopg.connect(resolved, autocommit=True) as conn:
        conn.execute(
            "DELETE FROM memories WHERE tenant_id = %s AND subject_type = 'customer' "
            "AND COALESCE(subject_id, '') = %s AND metadata->>'source' = %s "
            "AND id <> %s",
            (tenant_id, cust_id, MEMORY_SOURCE, mem_id),
        )
    return mem_id


def enrich_lead(
    tenant_id: str, customer_id: str, *, dsn: str | None = None,
) -> dict[str, Any]:
    """Evidence-cited public-web enrichment for ONE customer (operator-initiated).

    Loads the customer's real row (name, city, business email domain, CSV
    interests), runs 1–3 cited queries through the shared research seam, applies
    the citation gate and the sensitive-trait post-filter, and — only when at
    least one cited fact survives — writes ONE replaceable customer memory the
    strategist/copywriter can cite.

    Returns the honest result::

        {"found": [{quote, url, query, angle, source_type, login_walled}, ...],
         "misses": [{query, note}, ...],
         "suppressed": <n facts dropped by the sensitive-trait filter>,
         "memory_id": "mem_..." | None}

    Zero surviving facts → no memory write and ``memory_id=None`` (an honest miss,
    never a fabricated profile). Raises ``LookupError`` for an unknown customer."""
    from studio.customer_research import lookup_lead

    facts = lookup_lead(tenant_id, customer_id=customer_id, dsn=dsn)
    if facts is None:
        raise LookupError(
            f"no customer {customer_id!r} on file for tenant {tenant_id!r}"
        )
    queries = build_enrichment_queries(facts)
    if not queries:
        return {
            "found": [], "misses": [], "suppressed": 0, "memory_id": None,
            "note": "no name on file for this customer — nothing honest to search",
        }
    found, misses = _collect_cited_facts(queries)
    kept, suppressed = suppress_sensitive(found)
    memory_id = _write_enrichment_memory(tenant_id, facts, kept, dsn=dsn) if kept else None
    return {
        "found": kept,
        "misses": misses,
        "suppressed": suppressed,
        "memory_id": memory_id,
    }


# --------------------------------------------------------------------------- #
# Read side — surfacing the enrichment to the strategist/copywriter dossier.
# --------------------------------------------------------------------------- #
def enrichment_memory(facts: dict[str, Any] | None) -> dict[str, Any] | None:
    """The customer's enrichment memory out of already-loaded ``facts['memories']``
    (the ``lookup_lead`` shape), or honest ``None`` when the lead has none. Pure —
    no DB read; tolerates dict rows and :class:`memory.store.Memory` objects."""
    for m in (facts or {}).get("memories") or []:
        md = m.get("metadata") if isinstance(m, dict) else getattr(m, "metadata", None)
        if (md or {}).get("source") == MEMORY_SOURCE:
            text = m.get("text") if isinstance(m, dict) else getattr(m, "text", "")
            return {"text": str(text or ""), "metadata": dict(md or {})}
    return None


def enrichment_prompt_lines(facts: dict[str, Any] | None) -> list[str]:
    """The clearly-labeled PUBLIC-WEB ENRICHMENT block for the per-lead dossier /
    copywriter prompt — ``[]`` when the lead has no enrichment memory, so the
    prompt stays honestly silent. Each line is a stored fact WITH its URL inline,
    so a draft that references one can cite it."""
    mem = enrichment_memory(facts)
    if mem is None:
        return []
    fact_lines = [
        ln for ln in (mem["text"] or "").splitlines() if ln.strip().startswith("- ")
    ]
    if not fact_lines:
        return []
    return [
        "",
        "# PUBLIC-WEB ENRICHMENT (verified public-web evidence about the recipient, "
        "gathered by the operator-initiated enrichment lookup — every fact carries "
        "its source URL; reference one ONLY when it is unmistakably about them, and "
        "add nothing beyond what is quoted):",
        *fact_lines,
    ]
