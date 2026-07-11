"""LIVE competitor discovery — ToS-COMPLIANT, official channels ONLY.

The operator's ask ("go research ~10 competitors in our niche/city, score them,
let me pick the most relevant one to mold") lands here, in FRONT of the existing
pick-pause (:func:`studio.competitor_flow.competitor_gate`). Two data paths, and
ONLY these two (hard product rule — NO headless / logged-in scraping of
instagram.com or facebook.com, ever):

  (a) **Firecrawl public-web SEARCH** (the existing secure, gated client in
      :mod:`research.providers.firecrawl`) finds PUBLIC pages that name
      competitor Instagram accounts in the tenant's niche + city — "best
      {niche} {city} instagram" style queries; handles are parsed from result
      urls/snippets (``instagram.com/<handle>``, ``@handle`` mentions).
  (b) **Meta's OFFICIAL Business Discovery Graph API** reads each candidate's
      public business profile + recent media with the operator's OWN publish
      credentials (``META_IG_USER_ID`` + ``META_PAGE_TOKEN``, ``appsecret_proof``
      signed when ``META_APP_SECRET`` is set) — the same env the publish path
      uses. One GET per handle; a non-business account comes back as a real
      OAuth error and is surfaced as an honest per-handle miss, never fabricated.

The fetched posts are UPSERTed into the same ``competitor_posts`` table the
operator-upload path fills (``source='discovery'``, deterministic id from
tenant+permalink so re-runs never duplicate) and scored by the EXISTING
deterministic :func:`studio.competitor_intel.score_posts` — no second scorer.

HONESTY: captions are stored VERBATIM as operator-facing review reference ONLY
(the mold path's never-copy guard, :func:`studio.competitor_flow.copies_verbatim`,
already clamps every drafted output); metrics carry only what the Graph API
actually returned; a missing key / empty search / all-miss run returns honest
zero counts with named reasons — never an invented handle, post, or number.
Tokens ride the Authorization header (never a URL) and are scrubbed from every
error message; they are never logged or echoed.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

SOURCE_DISCOVERY = "discovery"

_GRAPH = "https://graph.facebook.com/v25.0"

# The ONE Business Discovery read per handle: profile + last 12 media with the
# engagement fields the scorer consumes. Kept as a single template so the
# request-shape test pins the exact fields string we send.
BUSINESS_DISCOVERY_FIELDS = (
    "business_discovery.username({handle})"
    "{{username,followers_count,media_count,"
    "media.limit(12){{id,caption,like_count,comments_count,media_type,"
    "permalink,timestamp}}}}"
)

# instagram.com path segments that are product routes, never account handles.
_RESERVED_IG_SEGMENTS = frozenset({
    "p", "tv", "reel", "reels", "stories", "explore", "accounts", "about",
    "developer", "developers", "directory", "tags", "legal", "web", "api",
    "blog", "press", "privacy", "help", "invites", "graphql",
})

_IG_URL_RE = re.compile(r"instagram\.com/([A-Za-z0-9_.]{2,30})", re.IGNORECASE)
# Lookbehind keeps email local-parts ("info@studio.com") and doubled @@ out.
_MENTION_RE = re.compile(r"(?<![\w.@])@([A-Za-z0-9_.]{2,30})")
# A "handle" ending in a TLD is a domain that leaked through an @-mention.
_TLD_RE = re.compile(r"\.(com|net|org|io|co|us|uk|dev|app|shop)$", re.IGNORECASE)
_HANDLE_RE = re.compile(r"[a-z0-9_.]{2,30}")

# Words that carry no niche signal when mining pack positioning / plan goals.
_TERM_STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "of", "in", "on", "at", "for", "with", "to",
    "our", "my", "your", "near", "specializing", "specialising", "based",
    "create", "make", "post", "posts", "campaign", "instagram", "facebook",
    "book", "clients", "more", "new", "best", "top",
})

# Test seam: ONE urlopen indirection so the Graph GET is mockable with no
# network (this sandbox blocks graph.facebook.com; live runs on the operator box).
_urlopen = urllib.request.urlopen


class BusinessDiscoveryError(RuntimeError):
    """A Business Discovery read failed — carries the REAL Graph error detail
    (token scrubbed), e.g. the OAuth 'not a business account' miss. Never a
    fabricated profile."""


def _scrub(text: str, token: str) -> str:
    """Defense in depth: the token can never reach a log or an operator surface."""
    if token and token in (text or ""):
        return text.replace(token, "***")
    return text or ""


def _proof(app_secret: str, token: str) -> str:
    return hmac.new(app_secret.encode(), token.encode(), hashlib.sha256).hexdigest()


# --------------------------------------------------------------------------- #
# (a) Firecrawl public-web search → candidate handles.
# --------------------------------------------------------------------------- #
def _clean_handle(raw: str) -> str | None:
    """Canonical lowercase handle, or ``None`` when the capture is a product
    route / domain / non-handle. Pure."""
    h = (raw or "").strip().strip(".").lower()
    if not _HANDLE_RE.fullmatch(h):
        return None
    if h in _RESERVED_IG_SEGMENTS or _TLD_RE.search(h):
        return None
    return h


def extract_handles(
    hits: list[Any],
    *,
    own_handles: frozenset[str] | set[str] = frozenset(),
    limit: int = 10,
) -> list[dict[str, Any]]:
    """PURE handle extraction from Firecrawl-shaped hits (``url``/``title``/
    ``snippet``): ``instagram.com/<handle>`` patterns anywhere in the hit, plus
    ``@handle`` mentions in title/snippet. De-duped in first-seen order; the
    tenant's OWN handle(s) dropped; each candidate carries its evidence
    (the REAL hit url + title it was parsed from — never invented)."""
    own = {h.strip().lstrip("@").lower() for h in own_handles if h}
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for hit in hits:
        url = str(getattr(hit, "url", "") or "")
        title = getattr(hit, "title", None)
        snippet = getattr(hit, "snippet", None)
        blob = " ".join(filter(None, [url, title, snippet]))
        candidates = [m.group(1) for m in _IG_URL_RE.finditer(blob)]
        candidates += [
            m.group(1)
            for m in _MENTION_RE.finditer(" ".join(filter(None, [title, snippet])))
        ]
        for raw in candidates:
            h = _clean_handle(raw)
            if h is None or h in seen or h in own:
                continue
            seen.add(h)
            out.append({"handle": h, "evidence_url": url, "evidence_title": title})
            if len(out) >= max(1, limit):
                return out
    return out


def _default_search(env) -> Callable[..., list[Any]] | None:
    """The real Firecrawl search when ``FIRECRAWL_API_KEY`` is armed, else
    ``None`` (the caller degrades to honest-empty — no key, no live call)."""
    key = (env.get("FIRECRAWL_API_KEY") or "").strip()
    if not key:
        return None
    from research.providers.firecrawl import FirecrawlProvider

    return FirecrawlProvider(api_key=key, enabled=True).search


def discover_candidate_handles(
    tenant_id: str,
    *,
    niche_terms: list[str],
    city: str | None,
    limit: int = 10,
    search: Callable[..., list[Any]] | None = None,
    env=None,
) -> list[dict[str, Any]]:
    """Find candidate competitor Instagram handles via PUBLIC web search only
    (Firecrawl — never an instagram.com crawl). Queries name the niche + city;
    handles are parsed from the REAL result urls/titles/snippets. Returns
    ``[{handle, evidence_url, evidence_title}]`` — de-duped, own handle dropped,
    capped at ``limit``. Honest-empty ``[]`` on no key / no results / all
    queries failing."""
    e = env if env is not None else os.environ
    terms = [str(t).strip() for t in (niche_terms or []) if str(t or "").strip()]
    if not terms:
        return []
    searcher = search or _default_search(e)
    if searcher is None:
        return []  # no FIRECRAWL_API_KEY — honest-empty, never a fabricated handle
    niche = " ".join(terms[:4])
    loc = (city or "").strip()
    queries: list[str] = []
    for q in (
        " ".join(filter(None, ["best", niche, loc, "instagram"])),
        " ".join(filter(None, [niche, loc, "top studios"])),
        " ".join(filter(None, ["top", niche, "accounts instagram", loc])),
    ):
        if q not in queries:
            queries.append(q)
    hits: list[Any] = []
    for q in queries:
        try:
            hits.extend(searcher(q, limit=max(5, limit)))
        except Exception:  # noqa: BLE001 — one failed query never fabricates hits
            continue
    return extract_handles(
        hits, own_handles={tenant_id.strip().lstrip("@").lower()}, limit=limit
    )


# --------------------------------------------------------------------------- #
# (b) Meta OFFICIAL Business Discovery — one Graph GET per handle.
# --------------------------------------------------------------------------- #
def business_discovery(
    handle: str,
    *,
    ig_user_id: str,
    page_token: str,
    app_secret: str | None = None,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """ONE official Business Discovery read for ``handle``:

        GET /v25.0/{ig_user_id}?fields=business_discovery.username({handle}){…}

    Token in the ``Authorization: Bearer`` header — NEVER the URL (mirrors
    connectors/ig.py); ``appsecret_proof`` rides as a query param when the app
    secret is present (a non-reversible HMAC, safe there). Returns the parsed
    ``{username, followers_count, media_count, media: [...]}`` or raises
    :class:`BusinessDiscoveryError` carrying the REAL Graph error (token
    scrubbed) — a non-business account surfaces as the API's own OAuth error,
    never as a fabricated profile."""
    h = (handle or "").strip().lstrip("@")
    # The handle is embedded in the fields expression — validate it hard so a
    # parsed-from-the-web string can never smuggle extra Graph syntax.
    if not re.fullmatch(r"[A-Za-z0-9_.]{1,30}", h):
        raise BusinessDiscoveryError(f"invalid instagram handle {h!r}")
    if not (str(ig_user_id or "").strip() and (page_token or "").strip()):
        raise BusinessDiscoveryError(
            "Meta Business Discovery credentials missing "
            "(META_IG_USER_ID / META_PAGE_TOKEN)"
        )
    query: dict[str, str] = {"fields": BUSINESS_DISCOVERY_FIELDS.format(handle=h)}
    if app_secret:
        query["appsecret_proof"] = _proof(app_secret, page_token)
    req = urllib.request.Request(
        f"{_GRAPH}/{str(ig_user_id).strip()}?{urllib.parse.urlencode(query)}",
        headers={"Authorization": f"Bearer {page_token}"},
        method="GET",
    )
    try:
        with _urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:500]
        raise BusinessDiscoveryError(
            f"@{h}: HTTP {exc.code}: {_scrub(_graph_error_detail(body), page_token)}"
        ) from None
    except Exception as exc:  # noqa: BLE001 — network failure, honest detail
        raise BusinessDiscoveryError(f"@{h}: {_scrub(str(exc), page_token)}") from None

    bd = data.get("business_discovery") if isinstance(data, dict) else None
    if not isinstance(bd, dict):
        raise BusinessDiscoveryError(
            f"@{h}: Graph returned no business_discovery node — the account is "
            "not readable via the official API"
        )
    # Graph nests media as {"data": [...]}; tolerate a bare list defensively.
    media_node = bd.get("media") or {}
    media = media_node.get("data") if isinstance(media_node, dict) else media_node
    return {
        "username": str(bd.get("username") or h),
        "followers_count": bd.get("followers_count"),
        "media_count": bd.get("media_count"),
        "media": [m for m in (media or []) if isinstance(m, dict)],
    }


def _graph_error_detail(raw: str) -> str:
    """The REAL Graph error (message/type/code) from an error body — never a token."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return (raw or "")[:300]
    err = data.get("error") if isinstance(data, dict) else None
    if isinstance(err, dict):
        msg = err.get("message") or err.get("type") or ""
        etype = err.get("type")
        code = err.get("code")
        bits = [str(msg)]
        if etype and etype not in str(msg):
            bits.append(str(etype))
        if code is not None:
            bits.append(f"code {code}")
        return " — ".join(b for b in bits if b)
    return (raw or "")[:300]


# --------------------------------------------------------------------------- #
# Tenant niche/city grounding (pack positioning → plan goal → display name).
# --------------------------------------------------------------------------- #
def _terms(text: str) -> list[str]:
    out: list[str] = []
    for w in re.split(r"[^A-Za-z0-9-]+", text or ""):
        t = w.strip("-").lower()
        if len(t) >= 2 and t not in _TERM_STOPWORDS and t not in out:
            out.append(t)
    return out


def resolve_niche_city(
    tenant_id: str, plan: Any = None, topic: str = ""
) -> tuple[list[str], str, str]:
    """``(niche_terms, city, evidence)`` from REAL tenant grounding only:

    1. an explicit operator ``topic`` wins for the niche terms;
    2. else the tenant pack's ``voice.positioning`` (e.g. "a Las Vegas tattoo
       studio") — its capitalized run also yields the ``city``;
    3. else the plan's goal keywords;
    4. else the pack display name (brand words — last resort, still real).

    ``evidence`` names which source resolved, so the run note is traceable.
    Honest-empty ``([], "", "none")`` when nothing is on file — the caller skips
    discovery rather than inventing a niche."""
    positioning = display = ""
    try:
        from config.loader import load_pack

        pack = load_pack(tenant_id)
        positioning = (pack.voice.positioning or "").strip()
        display = (pack.display_name or "").strip()
    except Exception:  # no/corrupt pack degrades to plan-only grounding
        pass

    # City = the first run of consecutive Capitalized words in the positioning
    # ("a Brooklyn fine-line …" → Brooklyn; "… Las Vegas tattoo …" → Las Vegas).
    city_parts: list[str] = []
    for raw in positioning.split():
        w = raw.strip(",.;:()")
        if w[:1].isupper() and w.lower() not in _TERM_STOPWORDS:
            city_parts.append(w)
        elif city_parts:
            break
    city = " ".join(city_parts)
    city_words = {w.lower() for w in city_parts}

    topic_terms = _terms(topic)
    if topic_terms:
        return topic_terms, city, "operator topic"
    if positioning:
        niche = [t for t in _terms(positioning) if t not in city_words]
        if niche:
            return niche, city, "tenant pack positioning"
    goal_terms = _terms(str(getattr(plan, "goal", "") or ""))
    if goal_terms:
        return goal_terms[:6], city, "plan goal keywords"
    if display:
        return _terms(display), city, "tenant display name"
    return [], city, "none"


# --------------------------------------------------------------------------- #
# Persistence — same table, same deterministic id seam as the upload path.
# --------------------------------------------------------------------------- #
def upsert_discovered_posts(
    tenant_id: str,
    profiles: list[tuple[dict[str, Any], dict[str, Any]]],
    *,
    niche: str | None = None,
    dsn: str | None = None,
) -> int:
    """UPSERT the fetched posts into ``competitor_posts`` (``source='discovery'``,
    id = the SAME deterministic (tenant, permalink) hash the upload path uses, so
    re-runs and upload/discovery overlaps never duplicate; a re-run refreshes the
    metrics/caption with the latest official-API numbers). ``metrics`` carries
    ONLY what the API returned — likes/comments per post, the profile's follower
    count, and the derived ``engagement_rate = (likes+comments)/max(followers,1)``
    (absent when the API reported no engagement fields). Returns rows written."""
    from studio.competitor_intel import (
        _connect,
        _parse_posted_at,
        _post_id,
        ensure_schema,
    )

    ensure_schema(dsn)
    n = 0
    with _connect(dsn) as conn:
        for cand, prof in profiles:
            handle = str(prof.get("username") or cand.get("handle") or "").strip().lstrip("@")
            followers = prof.get("followers_count")
            if not isinstance(followers, (int, float)) or isinstance(followers, bool):
                followers = None
            for m in prof.get("media") or []:
                permalink = str(m.get("permalink") or "").strip()
                caption = str(m.get("caption") or "")
                if not (permalink or handle):
                    continue
                metrics: dict[str, Any] = {}
                for src_key, out_key in (("like_count", "likes"), ("comments_count", "comments")):
                    v = m.get(src_key)
                    if isinstance(v, (int, float)) and not isinstance(v, bool):
                        metrics[out_key] = v
                if followers is not None:
                    metrics["followers"] = followers
                    if "likes" in metrics or "comments" in metrics:
                        metrics["engagement_rate"] = round(
                            (metrics.get("likes", 0) + metrics.get("comments", 0))
                            / max(followers, 1),
                            6,
                        )
                conn.execute(
                    "INSERT INTO competitor_posts "
                    "(id, tenant_id, handle, url, platform, caption, visual_tags, "
                    " metrics, niche, posted_at, source) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s) "
                    "ON CONFLICT (id) DO UPDATE SET "
                    "  handle = EXCLUDED.handle, "
                    "  caption = EXCLUDED.caption, "
                    "  metrics = EXCLUDED.metrics, "
                    "  posted_at = COALESCE(EXCLUDED.posted_at, competitor_posts.posted_at), "
                    "  niche = COALESCE(EXCLUDED.niche, competitor_posts.niche), "
                    "  source = EXCLUDED.source",
                    (
                        _post_id(tenant_id, permalink, handle, caption),
                        tenant_id,
                        handle or permalink,
                        permalink or None,
                        "instagram",
                        caption or None,  # VERBATIM — operator review reference only
                        json.dumps([]),
                        json.dumps(metrics),
                        (niche or "").strip() or None,
                        _parse_posted_at(m.get("timestamp")),
                        SOURCE_DISCOVERY,
                    ),
                )
                n += 1
    return n


# --------------------------------------------------------------------------- #
# The orchestrator the gate + host tool call.
# --------------------------------------------------------------------------- #
def run_discovery(
    tenant_id: str,
    *,
    plan: Any = None,
    limit_handles: int = 10,
    dsn: str | None = None,
    topic: str = "",
    search: Callable[..., list[Any]] | None = None,
    fetch: Callable[[str], dict[str, Any]] | None = None,
    env=None,
    time_budget_s: float = 60.0,
    clock: Callable[[], float] | None = None,
) -> dict[str, Any]:
    """Full ToS-compliant discovery: public-web candidates → one official
    Business Discovery read per handle (best-effort; every miss recorded with
    its REAL reason) → upsert into ``competitor_posts`` → the EXISTING
    deterministic :func:`studio.competitor_intel.score_posts`. Returns honest
    counts only:

        {"ok", "candidates", "fetched", "posts", "misses": [{handle, reason}],
         "top": [{postId, handle, url, totalScore, whyItWorked}], "note",
         "niche_terms", "city"}

    Bounded by ``time_budget_s`` (handles left when the budget runs out become
    misses, honestly noted). Idempotent: re-runs UPSERT on the deterministic id
    — no duplicate rows. ``search``/``fetch``/``env``/``clock`` are test seams;
    the defaults are the real Firecrawl client + Graph GET + os.environ."""
    tick = clock or time.monotonic
    started = tick()
    e = env if env is not None else os.environ
    niche_terms, city, evidence = resolve_niche_city(tenant_id, plan=plan, topic=topic)
    out: dict[str, Any] = {
        "ok": False, "candidates": 0, "fetched": 0, "posts": 0,
        "misses": [], "top": [], "note": "",
        "niche_terms": niche_terms, "city": city or None,
    }
    if not niche_terms:
        out["note"] = (
            "no niche terms resolvable (no pack positioning, no plan goal, no "
            "topic) — discovery skipped, nothing invented"
        )
        return out

    candidates = discover_candidate_handles(
        tenant_id, niche_terms=niche_terms, city=city,
        limit=max(1, limit_handles), search=search, env=e,
    )
    out["candidates"] = len(candidates)
    if not candidates:
        keyed = bool(search) or bool((e.get("FIRECRAWL_API_KEY") or "").strip())
        out["note"] = (
            f"web search ({evidence}: {' '.join(niche_terms[:4])}"
            + (f" / {city}" if city else "")
            + ") produced no candidate instagram handles"
            + ("" if keyed else " — FIRECRAWL_API_KEY not set")
        )
        return out

    fetcher = fetch
    if fetcher is None:
        ig_user_id = (e.get("META_IG_USER_ID") or "").strip()
        page_token = (e.get("META_PAGE_TOKEN") or "").strip()
        app_secret = (e.get("META_APP_SECRET") or "").strip() or None
        if not (ig_user_id and page_token):
            out["misses"] = [
                {"handle": c["handle"],
                 "reason": "Meta Business Discovery credentials not configured "
                           "(META_IG_USER_ID / META_PAGE_TOKEN)"}
                for c in candidates
            ]
            out["note"] = (
                f"found {len(candidates)} candidate handle(s) but Meta "
                "credentials are not configured — no profiles read"
            )
            return out

        def fetcher(h: str) -> dict[str, Any]:
            return business_discovery(
                h, ig_user_id=ig_user_id, page_token=page_token,
                app_secret=app_secret,
            )

    profiles: list[tuple[dict[str, Any], dict[str, Any]]] = []
    misses: list[dict[str, str]] = []
    for c in candidates:
        if tick() - started > time_budget_s:
            misses.append({
                "handle": c["handle"],
                "reason": f"skipped — discovery time budget (~{int(time_budget_s)}s) exhausted",
            })
            continue
        try:
            profiles.append((c, fetcher(c["handle"])))
        except Exception as exc:  # noqa: BLE001 — per-handle best effort, REAL reason kept
            misses.append({"handle": c["handle"], "reason": str(exc)[:300]})
    out["fetched"] = len(profiles)
    out["misses"] = misses
    if not profiles:
        out["note"] = (
            f"discovering competitors live: {len(candidates)} handles found, "
            f"0 profiles read ({len(misses)} miss(es)) — nothing stored"
        )
        return out

    n_posts = upsert_discovered_posts(
        tenant_id, profiles, niche=" ".join(niche_terms[:4]), dsn=dsn
    )
    out["posts"] = n_posts

    from studio.competitor_intel import score_posts

    scored = score_posts(
        tenant_id,
        artist=(str(getattr(plan, "artist", "") or "").strip() or None),
        dsn=dsn,
    )
    out["top"] = [
        {
            "postId": p["id"],
            "handle": p.get("handle"),
            "url": p.get("url"),
            "totalScore": (
                float(p["total_score"]) if p.get("total_score") is not None else None
            ),
            "whyItWorked": p.get("why_it_worked"),
        }
        for p in scored[:6]
    ]
    out["ok"] = n_posts > 0
    out["note"] = (
        f"discovering competitors live: {len(candidates)} handles found, "
        f"{len(profiles)} profiles read, {n_posts} posts scored"
    )
    return out
