"""Competitor creative intelligence (Social Growth) — inspiration to MOLD, never copy.

The operator uploads competitor posts (CSV/JSON with handle/url/caption/metrics —
NO scraping; the live official-API path is :mod:`studio.competitor_discovery`,
which upserts into the same table with ``source='discovery'``). This module
turns those rows into a pattern the IG drafting crew can mold:

  * :func:`ingest_competitor_csv` stores the rows in ``competitor_posts``,
    idempotent on (tenant, url) via a deterministic id. ``metrics`` carries ONLY
    the numbers actually provided — a missing likes/views column stays absent,
    never zero-filled as if the operator had reported a zero.
  * :func:`score_posts` scores every post 0–10 DETERMINISTICALLY with a
    per-parameter breakdown persisted in ``scores``. Every component whose
    underlying data is absent scores ``None`` and is EXCLUDED from the weighted
    total (the remaining weights renormalize) — never a fake 0 or a fake 5.
  * :func:`best_pattern` deconstructs the top post into a reusable SHAPE (hook
    line, structure outline, emotional angle, CTA, visual pattern) with pure
    keyword heuristics. ONE optional policy-clamped LLM refinement runs only when
    ``ANTHROPIC_API_KEY`` is armed (mirrors studio/supervisor_control.py); the
    deterministic path is complete on its own — honest skip otherwise.
  * :func:`render_competitor_pattern_block` renders the brief block that ORDERS
    the drafter: structure/hook-shape/CTA-shape from this pattern; artwork ONLY
    from our library; wording in OUR brand voice; offers ONLY substantiated
    codes; NEVER copy competitor sentences verbatim.

Design rule (operator's order): a competitor post is inspiration for the SHAPE of
our post — the artwork, the wording, and the offers are always OURS.

HONESTY: every stored field is exactly what the operator provided; every score
component names its evidence and is ``None`` when the data to compute it does not
exist; an empty table renders an honest "no competitor data" statement.
Runtime DDL twin: ``infra/initdb/26-competitor-posts.sql``.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import re
from datetime import datetime, timezone
from typing import Any

# Same normalization + word-token overlap the artwork selector uses, so a
# competitor caption matches our tags exactly the way a post theme does.
from studio.artwork_select import _norm, _overlap

SOURCE_UPLOAD = "upload"

_DEFAULT_DSN = "postgresql://scalers:scalers@localhost:5432/scalers"

# The header shape the operator's export carries. Tolerant: any subset beyond
# handle(+url) parses; missing metric columns stay ABSENT in ``metrics``.
COMPETITOR_HEADERS = (
    "handle", "url", "platform", "caption", "likes", "comments", "views",
    "shares", "saves", "niche", "posted_at",
)
_METRIC_COLS = ("likes", "comments", "views", "shares", "saves")

# Columns that mark a CUSTOMER list, never a competitor export — detection must
# not swallow an audience CSV (those rows are send targets, not intel).
_CUSTOMER_MARKERS = frozenset({"email", "phone", "first_name", "last_name"})

_DDL = """
CREATE TABLE IF NOT EXISTS competitor_posts (
    id            TEXT PRIMARY KEY,
    tenant_id     TEXT NOT NULL,
    handle        TEXT NOT NULL,
    url           TEXT,
    platform      TEXT,
    caption       TEXT,
    visual_tags   JSONB NOT NULL DEFAULT '[]'::jsonb,
    metrics       JSONB NOT NULL DEFAULT '{}'::jsonb,
    niche         TEXT,
    posted_at     TIMESTAMPTZ,
    scores        JSONB NOT NULL DEFAULT '{}'::jsonb,
    total_score   NUMERIC,
    why_it_worked TEXT,
    source        TEXT NOT NULL DEFAULT 'upload',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS competitor_posts_tenant_score_idx
    ON competitor_posts (tenant_id, total_score);
"""


def _dsn(dsn: str | None) -> str:
    return dsn or os.environ.get(
        "ENGINE_DATABASE_URL", _DEFAULT_DSN
    )


def _connect(dsn: str | None):
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(_dsn(dsn), row_factory=dict_row, autocommit=True)


def ensure_schema(dsn: str | None = None) -> None:
    with _connect(dsn) as conn:
        conn.execute(_DDL)


# --------------------------------------------------------------------------- #
# Ingest — operator-provided CSV/JSON only (no scraping, no fabricated metrics).
# --------------------------------------------------------------------------- #
def looks_like_competitor_csv(content: str) -> bool:
    """PURE header-shape detection for the upload branch chain: a competitor
    export names a bare ``handle`` column plus at least one competitor-specific
    column, and carries NO customer contact columns (email/phone → audience)."""
    cols = {c.lower() for c in _header_columns(content)}
    if not cols or cols & _CUSTOMER_MARKERS:
        return False
    return "handle" in cols and bool(
        cols & {"url", "likes", "comments", "views", "shares", "saves", "niche"}
    )


def _header_columns(content: str) -> list[str]:
    """The upload's column names — CSV header row, or a JSON array's object keys."""
    text = (content or "").lstrip("﻿").strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            data = json.loads(text)
        except Exception:
            return []
        first = next((r for r in data if isinstance(r, dict)), None) if isinstance(data, list) else None
        return [str(k).strip() for k in (first or {})]
    try:
        row = next(csv.reader(io.StringIO(text)))
    except Exception:
        return []
    return [(c or "").strip() for c in row]


def _rows_from_content(content: str) -> list[dict[str, Any]]:
    """Tolerant parse of the operator's upload: CSV (DictReader) or a JSON array
    of objects. Keys lowercased/stripped; values kept as provided (strings from
    CSV, native types from JSON). Never invents a column."""
    text = (content or "").lstrip("﻿").strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            data = json.loads(text)
        except Exception:
            return []
        return [
            {str(k).strip().lower(): v for k, v in r.items()}
            for r in (data if isinstance(data, list) else [])
            if isinstance(r, dict)
        ]
    reader = csv.DictReader(io.StringIO(text))
    return [
        {(k or "").strip().lower(): (v if v is not None else "") for k, v in r.items()}
        for r in reader
    ]


def _parse_metric(raw: Any) -> int | float | None:
    """One provided metric value → number, or ``None`` when absent/unparseable.
    NEVER coerces absence to 0 — an empty cell means 'not reported'."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return raw
    s = str(raw).strip().replace(",", "")
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        try:
            return float(s)
        except ValueError:
            return None  # honest: an unparseable cell is not a number we report


def _parse_posted_at(raw: Any) -> datetime | None:
    """Tolerant timestamp parse; ``None`` (not now(), not epoch) when unparseable."""
    s = str(raw or "").strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _split_tags(raw: Any) -> list[str]:
    """visual_tags cell → list. JSON lists pass through; CSV cells split on ;/|
    (comma is the CSV delimiter)."""
    if isinstance(raw, list):
        return [str(t).strip() for t in raw if str(t).strip()]
    return [t.strip() for t in re.split(r"[;|]", str(raw or "")) if t.strip()]


def _post_id(tenant_id: str, url: str, handle: str, caption: str) -> str:
    """Deterministic id — idempotent on (tenant, url); rows without a url key on
    (tenant, handle, caption prefix) so a re-upload still creates no duplicates."""
    key = url.strip().lower() or f"{handle.strip().lower()}|{(caption or '')[:120]}"
    return "cmp_" + hashlib.sha1(f"{tenant_id}|{key}".encode()).hexdigest()[:16]


def ingest_competitor_csv(
    tenant_id: str, content: str, dsn: str | None = None
) -> dict[str, Any]:
    """Ingest an operator-provided competitor export (CSV or JSON array) into
    ``competitor_posts``. Idempotent on (tenant, url): re-uploading the same file
    inserts nothing new. Returns honest counts:

        {"ok", "rows", "ingested", "duplicates", "skipped", "handles"}

    Missing metric columns stay ABSENT in ``metrics`` (never zero-filled); an
    unparseable ``posted_at`` stays NULL. Rows with neither handle nor url are
    skipped (nothing to attribute the post to), counted in ``skipped``."""
    ensure_schema(dsn)
    rows = _rows_from_content(content)
    ingested = duplicates = skipped = 0
    handles: list[str] = []
    seen_handles: set[str] = set()
    with _connect(dsn) as conn:
        for r in rows:
            handle = str(r.get("handle") or "").strip().lstrip("@")
            url = str(r.get("url") or "").strip()
            if not handle and not url:
                skipped += 1
                continue
            caption = str(r.get("caption") or "").strip()
            metrics = {
                k: v for k in _METRIC_COLS if (v := _parse_metric(r.get(k))) is not None
            }
            cur = conn.execute(
                "INSERT INTO competitor_posts "
                "(id, tenant_id, handle, url, platform, caption, visual_tags, "
                " metrics, niche, posted_at, source) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s) "
                "ON CONFLICT (id) DO NOTHING",
                (
                    _post_id(tenant_id, url, handle, caption),
                    tenant_id,
                    handle or url,
                    url or None,
                    str(r.get("platform") or "").strip().lower() or None,
                    caption or None,
                    json.dumps(_split_tags(r.get("visual_tags") or r.get("tags"))),
                    json.dumps(metrics),
                    str(r.get("niche") or "").strip() or None,
                    _parse_posted_at(r.get("posted_at")),
                    SOURCE_UPLOAD,
                ),
            )
            if cur.rowcount:
                ingested += 1
            else:
                duplicates += 1
            if handle and handle.lower() not in seen_handles:
                seen_handles.add(handle.lower())
                handles.append(handle)
    return {
        "ok": True,
        "rows": len(rows),
        "ingested": ingested,
        "duplicates": duplicates,
        "skipped": skipped,
        "handles": handles[:8],
    }


SOURCE_SCREENSHOT = "screenshot_upload"

# Leading "@handle" in the operator's prompt names the competitor; the rest is
# treated as the caption they transcribed (both optional, never invented).
_SHOT_PROMPT_RE = re.compile(r"^@([A-Za-z0-9._]{2,60})\b[\s:,—-]*(.*)$", re.S)


def record_screenshot_post(
    tenant_id: str,
    *,
    name: str,
    prompt: str | None,
    vlm: dict[str, Any],
    artifact_id: str,
    sha: str,
    dsn: str | None = None,
) -> dict[str, Any]:
    """File an uploaded competitor-post SCREENSHOT as a real ``competitor_posts``
    row whose ``visual_tags`` come from the VLM's image analysis — the image
    itself is researched, not just operator-typed metadata.

    * handle/caption parse from the operator prompt ("@inkhaus their spring flash
      drop" → handle=inkhaus, caption=the rest); absent → handle falls back to the
      filename stem, caption stays empty. Nothing is invented.
    * metrics stay ABSENT (a screenshot proves content, not engagement numbers) —
      scoring's None-exclusion renormalizes honestly.
    * idempotent on the image bytes: the same screenshot re-uploaded refreshes
      tags/caption on its ONE row (id from the content sha).
    """
    ensure_schema(dsn)
    text = (prompt or "").strip()
    handle, caption = "", text
    m = _SHOT_PROMPT_RE.match(text)
    if m:
        handle, caption = m.group(1), m.group(2).strip()
    if not handle:
        handle = re.sub(r"\.[A-Za-z0-9]+$", "", (name or "").strip()) or "unknown"

    buckets = (vlm or {}).get("tags") or {}
    visual_tags: list[str] = []
    for key in ("styles", "motifs", "color_mode", "mood", "complexity"):
        val = buckets.get(key)
        if isinstance(val, list):
            visual_tags.extend(str(t).strip() for t in val if str(t).strip())
        elif val and str(val).strip():
            visual_tags.append(str(val).strip())

    post_id = "cmp_" + hashlib.sha1(f"{tenant_id}|shot|{sha}".encode()).hexdigest()[:16]
    with _connect(dsn) as conn:
        cur = conn.execute(
            "INSERT INTO competitor_posts "
            "(id, tenant_id, handle, url, platform, caption, visual_tags, "
            " metrics, niche, posted_at, source) "
            "VALUES (%s,%s,%s,NULL,NULL,%s,%s::jsonb,'{}'::jsonb,NULL,NULL,%s) "
            "ON CONFLICT (id) DO UPDATE SET "
            "  handle = EXCLUDED.handle, caption = EXCLUDED.caption, "
            "  visual_tags = EXCLUDED.visual_tags",
            (post_id, tenant_id, handle, caption or None,
             json.dumps(visual_tags), SOURCE_SCREENSHOT),
        )
    return {
        "post_id": post_id,
        "handle": handle,
        "caption": caption or None,
        "visual_tags": visual_tags,
        "vlm_status": (vlm or {}).get("status"),
        "artifact_id": artifact_id,
        "ingested": bool(cur.rowcount),
    }


# --------------------------------------------------------------------------- #
# Deterministic 0–10 scoring. WEIGHTS below are the documented weighted sum;
# a component with no underlying data is None and EXCLUDED (renormalized).
# --------------------------------------------------------------------------- #
WEIGHTS: dict[str, float] = {
    # THEME RELEVANCE dominates when a campaign THEME is given (e.g. 'fine-line
    # botanical'): the competitor we mold must match the BRIEF, not merely the
    # artist's general niche. Without it, an off-theme color-realism competitor
    # (high engagement + matches Keebs' realism work) out-scored the actual
    # fine-line-botanical posts the operator asked for — the pick that shapes the
    # post looked irrelevant. None (excluded, renormalized) when no theme is given,
    # so an untargeted scoring is byte-identical to before.
    "theme_relevance": 0.40,   # niche+caption+visual_tags vs the CAMPAIGN theme
    "engagement_rate": 0.25,   # interactions/views — the strongest performance signal
    # FOLLOWER REACH (client's core note, PA meeting 2026-07-11): the old scorer
    # let a tiny account with a catchy caption out-rank a real top performer
    # (~100 likes vs the 20k–50k-like accounts the client actually wants to mold
    # from). For discovered posts the interactions/views rate is usually absent
    # (the Business Discovery API returns no view count), so raw REACH — account
    # size + absolute like volume — carries the "who is actually winning" signal.
    # Both log-scaled (10 ≈ 10k) and None (excluded) when the number is absent, so
    # nothing is fabricated; the config floors (min_followers / min_engagement_rate)
    # additionally hard-exclude tiny accounts before they ever reach this scorer.
    "follower_reach": 0.20,    # account size — big accounts rank above tiny ones
    "likes_weight": 0.15,      # absolute like volume (log scale) — the "50k likes"
    "comments_weight": 0.10,   # conversation volume (log scale)
    "shares_saves_weight": 0.10,  # amplification/bookmark intent (log scale)
    "niche_match": 0.15,       # niche+caption tokens vs OUR artist style tags
    "style_match": 0.15,       # caption+visual_tags vs OUR artwork library tags
    "recency": 0.10,           # fresher patterns matter more (linear decay over a year)
    "cta_strength": 0.10,      # deterministic CTA keyword heuristics
    "hook_strength": 0.05,     # deterministic first-sentence heuristics
}

_IMPERATIVE_VERBS = frozenset({
    "book", "dm", "message", "call", "tap", "click", "visit", "claim", "grab",
    "reply", "comment", "save", "share", "follow", "swipe", "shop", "join",
    "text", "order", "drop",
})
_URGENCY_WORDS = frozenset({
    "today", "now", "tonight", "limited", "only", "last", "ends", "hurry",
    "spots", "spot", "left", "final", "closing", "deadline", "week", "weekend",
})
_PROOF_WORDS = frozenset({
    "clients", "booked", "reviews", "rated", "years", "artists", "awarded",
    "healed", "sessions",
})
_OFFER_RE = re.compile(
    r"(\d+\s*%\s*off|\$\s*\d+|\bfree\b|\bdiscount\b|\bdeal\b|\boffer\b|"
    r"\bspecial\b|\bpromo\b|\bcode\s+[A-Za-z0-9]+)",
    re.IGNORECASE,
)
_LINK_IN_BIO_RE = re.compile(r"link\s+in\s+(?:the\s+)?bio", re.IGNORECASE)


def _word_tokens(*texts: str) -> set[str]:
    """Canonical word tokens (>=3 chars, same normalization as artwork_select) so
    'fine-line' in a caption matches our 'fine-line' tag."""
    out: set[str] = set()
    for text in texts:
        for w in re.split(r"[^A-Za-z0-9-]+", text or ""):
            n = _norm(w)
            if len(n) >= 3:
                out.add(n)
    return out


def _clamp10(x: float) -> float:
    return round(max(0.0, min(10.0, x)), 2)


def _log_scale(count: float) -> float:
    """0–10 from an absolute count: log10-scaled so 10 ≈ 10k interactions.
    Deterministic and monotonic; only ever called with a PROVIDED number."""
    return _clamp10(math.log10(count + 1) * 2.5)


def _first_sentence(caption: str) -> str:
    """The caption's first sentence, VERBATIM (first non-empty line, split on
    sentence enders). Empty string when the caption is empty."""
    for line in (caption or "").splitlines():
        line = line.strip()
        if line:
            return re.split(r"(?<=[.!?])\s+", line)[0]
    return ""


def _sentences(caption: str) -> list[str]:
    return [
        s.strip()
        for line in (caption or "").splitlines()
        for s in re.split(r"(?<=[.!?])\s+", line)
        if s.strip()
    ]


def _cta_signals(text: str) -> list[str]:
    """Which deterministic CTA signal families ``text`` carries (evidence names).
    Verb matching uses UNFILTERED word tokens — 'DM' is two chars and the >=3
    tag-token filter would silently drop the most common IG imperative."""
    low = (text or "").lower()
    words = {_norm(w) for w in re.findall(r"[A-Za-z']+", low)}
    signals: list[str] = []
    if words & _IMPERATIVE_VERBS:
        signals.append("imperative-verb")
    if _LINK_IN_BIO_RE.search(low):
        signals.append("link-in-bio")
    if words & _URGENCY_WORDS:
        signals.append("urgency")
    if _OFFER_RE.search(text or ""):
        signals.append("offer-mention")
    return signals


def score_components(
    post: dict[str, Any],
    style_tags: list[str],
    library_tags: list[str],
    *,
    theme_terms: list[str] | None = None,
    now: datetime | None = None,
) -> dict[str, float | None]:
    """PURE per-parameter scores (0–10 each) for one post. EVERY component whose
    underlying data is absent is ``None`` — the weighted sum excludes it. Formulas:

      * engagement_rate    — (likes+comments+shares+saves present)/views × 100,
                             capped at 10 (a 10% rate scores 10). None without views.
      * comments_weight    — log10(comments+1) × 2.5 (10 ≈ 10k). None without comments.
      * shares_saves_weight— log10(shares+saves+1) × 2.5 over the PROVIDED ones.
                             None when neither is provided.
      * niche_match        — 2.5 × |our style tags word-matching niche+caption|,
                             capped 10. None when we have no style tags or the post
                             has neither niche nor caption.
      * style_match        — 2.5 × |our library tags word-matching caption+visual_tags|,
                             capped 10. None when either side has no data.
      * recency            — 10 − age_days/36.5 (10 today, 0 at one year), floored 0.
                             None without posted_at.
      * cta_strength       — 2.5 per CTA signal family present in the caption
                             (imperative verb / link-in-bio / urgency / offer).
                             None without a caption.
      * hook_strength      — first sentence: question +3, opens on an imperative +3,
                             urgency word +2, a number +2 (capped 10). None without
                             a caption.
    """
    now = now or datetime.now(timezone.utc)
    caption = str(post.get("caption") or "")
    niche = str(post.get("niche") or "")
    visual_tags = [str(t) for t in (post.get("visual_tags") or [])]
    metrics = post.get("metrics") or {}
    likes, comments = metrics.get("likes"), metrics.get("comments")
    views, shares, saves = metrics.get("views"), metrics.get("shares"), metrics.get("saves")

    out: dict[str, float | None] = {}

    interactions = [v for v in (likes, comments, shares, saves) if v is not None]
    if views is not None and views > 0 and interactions:
        out["engagement_rate"] = _clamp10(sum(interactions) / views * 100)
    else:
        out["engagement_rate"] = None  # honest: no views (or no interactions) reported

    # REACH signals — raw account size + absolute like volume, log-scaled (10 ≈
    # 10k). These separate the real top performers from the tiny accounts the old
    # scorer over-rewarded on caption alone. None (excluded) when the number is
    # absent — never a fabricated zero.
    followers = metrics.get("followers")
    out["follower_reach"] = _log_scale(followers) if followers is not None else None
    out["likes_weight"] = _log_scale(likes) if likes is not None else None

    out["comments_weight"] = _log_scale(comments) if comments is not None else None

    provided_amp = [v for v in (shares, saves) if v is not None]
    out["shares_saves_weight"] = _log_scale(sum(provided_amp)) if provided_amp else None

    # THEME RELEVANCE — the competitor vs the CAMPAIGN brief ('fine-line botanical').
    # Scored the same way as niche/style match but against the operator's theme terms
    # (expanded through the same flora/fine-line synonym bridge the artwork ranker uses,
    # so 'botanical' matches a 'Dahlia'/'wildflower' caption). None (excluded) when the
    # campaign named no theme — untargeted scoring is unchanged.
    if theme_terms and (niche or caption or visual_tags):
        from studio.artwork_select import _expand_theme, _norm_set

        theme_cmp = _expand_theme(_norm_set(theme_terms))
        matched = theme_cmp & _word_tokens(niche, caption, " ".join(visual_tags))
        out["theme_relevance"] = _clamp10(2.5 * len(matched))
    else:
        out["theme_relevance"] = None

    if style_tags and (niche or caption):
        matched = _overlap(style_tags, _word_tokens(niche, caption))
        out["niche_match"] = _clamp10(2.5 * len(matched))
    else:
        out["niche_match"] = None

    if library_tags and (caption or visual_tags):
        matched = _overlap(library_tags, _word_tokens(caption, " ".join(visual_tags)))
        out["style_match"] = _clamp10(2.5 * len(matched))
    else:
        out["style_match"] = None

    posted_at = post.get("posted_at")
    if isinstance(posted_at, datetime):
        if posted_at.tzinfo is None:
            posted_at = posted_at.replace(tzinfo=timezone.utc)
        age_days = max(0.0, (now - posted_at).total_seconds() / 86400)
        out["recency"] = _clamp10(10.0 - age_days / 36.5)
    else:
        out["recency"] = None

    if caption.strip():
        out["cta_strength"] = _clamp10(2.5 * len(_cta_signals(caption)))
        hook = _first_sentence(caption)
        hook_words = _word_tokens(hook)
        pts = 0.0
        if "?" in hook:
            pts += 3
        first_word = _norm((re.findall(r"[A-Za-z']+", hook) or [""])[0])
        if first_word in _IMPERATIVE_VERBS:
            pts += 3
        if hook_words & _URGENCY_WORDS:
            pts += 2
        if re.search(r"\d", hook):
            pts += 2
        out["hook_strength"] = _clamp10(pts)
    else:
        out["cta_strength"] = None
        out["hook_strength"] = None
    return out


def meets_reach_floor(
    metrics: dict[str, Any] | None,
    *,
    min_followers: int | None = None,
    min_engagement_rate: float | None = None,
) -> bool:
    """Whether a post's PROVIDED metrics clear the tenant's reach floors — the
    hard gate that keeps tiny accounts out of the mold set (client's core note,
    PA meeting 2026-07-11: stop surfacing ~100-like accounts).

    HONESTY: a floor only rejects when the underlying metric is actually present
    AND below it. An ABSENT metric is never treated as a failing zero (we can't
    prove a real account is tiny just because the API didn't return the number),
    so a post with no follower count still passes the follower floor — it is the
    *scorer* that then ranks it below accounts with proven reach. ``None`` floors
    are inactive. ``engagement_rate`` here is the discovery-stored
    ``(likes+comments)/followers`` ratio, not the views-based score component."""
    m = metrics or {}
    if min_followers is not None:
        f = m.get("followers")
        if isinstance(f, (int, float)) and not isinstance(f, bool) and f < min_followers:
            return False
    if min_engagement_rate is not None:
        er = m.get("engagement_rate")
        if isinstance(er, (int, float)) and not isinstance(er, bool) and er < min_engagement_rate:
            return False
    return True


def weighted_total(components: dict[str, float | None]) -> float | None:
    """The documented weighted sum over PRESENT components only, renormalized so
    absent data never drags the total toward a fabricated 0. ``None`` when no
    component has data at all."""
    present = {k: v for k, v in components.items() if v is not None and k in WEIGHTS}
    if not present:
        return None
    weight_sum = sum(WEIGHTS[k] for k in present)
    return round(sum(WEIGHTS[k] * v for k, v in present.items()) / weight_sum, 2)


def _build_why(
    components: dict[str, float | None], total: float | None, post: dict[str, Any]
) -> str:
    """Deterministic, evidence-named rationale. Every clause traces to a computed
    component or a provided field; excluded (no-data) components are NAMED, so the
    operator sees what the score does NOT rest on."""
    if total is None:
        return (
            "Not scorable: no metrics, tags, caption, or date were provided for "
            "this post — nothing was assumed."
        )
    ranked = sorted(
        ((k, v) for k, v in components.items() if v is not None),
        key=lambda kv: (-kv[1], kv[0]),
    )
    top = ", ".join(f"{k} {v:g}/10" for k, v in ranked[:3])
    missing = sorted(k for k, v in components.items() if v is None)
    why = f"Scored {total:g}/10 on provided data. Strongest components: {top}."
    if missing:
        why += f" No data for {', '.join(missing)} — excluded from the total, not assumed."
    return why


def score_posts(
    tenant_id: str,
    *,
    artist: str | None = None,
    theme_terms: list[str] | None = None,
    dsn: str | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Score every stored competitor post for ``tenant_id`` against OUR OWN
    grounding (the artist's/library's real tags via studio.artwork_select), persist
    the breakdown (``scores`` / ``total_score`` / ``why_it_worked``), and return
    the posts sorted best-first (unscorable rows last). When ``theme_terms`` is
    given (the campaign brief, e.g. 'fine-line botanical'), the dominant
    ``theme_relevance`` component makes brief-relevant competitors rank first;
    without it the scoring is unchanged. Honest-empty ``[]`` when no competitor
    data is on file."""
    from studio.artwork_select import artist_styles, list_artwork

    ensure_schema(dsn)
    library = list_artwork(tenant_id, artist, dsn=dsn)
    if artist and not library:
        library = list_artwork(tenant_id, dsn=dsn)  # whole-studio fallback, still OURS
    style_tags = artist_styles(library)
    seen: set[str] = {_norm(t) for t in style_tags}
    library_tags = list(style_tags)
    for ref in library:
        for tag in ref.motifs:
            n = _norm(tag)
            if n and n not in seen:
                seen.add(n)
                library_tags.append(tag)

    with _connect(dsn) as conn:
        rows = conn.execute(
            "SELECT id, handle, url, platform, caption, visual_tags, metrics, "
            "niche, posted_at, source FROM competitor_posts WHERE tenant_id=%s "
            "ORDER BY created_at, id",
            (tenant_id,),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            post = dict(r)
            components = score_components(
                post, style_tags, library_tags, theme_terms=theme_terms, now=now
            )
            total = weighted_total(components)
            why = _build_why(components, total, post)
            conn.execute(
                "UPDATE competitor_posts SET scores=%s::jsonb, total_score=%s, "
                "why_it_worked=%s WHERE id=%s",
                (json.dumps(components), total, why, r["id"]),
            )
            post.update(scores=components, total_score=total, why_it_worked=why)
            if isinstance(post.get("posted_at"), datetime):
                post["posted_at"] = post["posted_at"].isoformat()
            out.append(post)
    out.sort(key=lambda p: (p["total_score"] is None, -(p["total_score"] or 0.0), p["id"]))
    return out


# --------------------------------------------------------------------------- #
# Best-pattern deconstruction — the SHAPE the drafter molds, never the words.
# --------------------------------------------------------------------------- #
def deconstruct_caption(
    caption: str, visual_tags: list[str] | None = None
) -> dict[str, Any]:
    """PURE deterministic deconstruction of a competitor caption:

        {hook_line, structure: [{part, text}], emotional_angle, cta, visual_pattern}

    ``hook_line`` is the first sentence VERBATIM (shape reference only —
    the render block forbids copying it). ``structure`` labels each sentence
    hook/context/proof/cta by heuristics: first sentence = hook; a trailing
    sentence with CTA signals = cta; digits/proof words = proof; else context.
    ``emotional_angle`` is a keyword label. Empty caption → honest Nones."""
    sentences = _sentences(caption)
    if not sentences:
        return {
            "hook_line": None, "structure": [], "emotional_angle": None,
            "cta": None,
            "visual_pattern": ", ".join(visual_tags or []) or None,
        }

    cta_text: str | None = None
    if _cta_signals(sentences[-1]):
        cta_text = sentences[-1]

    structure: list[dict[str, str]] = []
    for i, s in enumerate(sentences):
        if i == 0:
            part = "hook"
        elif cta_text is not None and i == len(sentences) - 1:
            part = "cta"
        elif re.search(r"\d", s) or (_word_tokens(s) & _PROOF_WORDS):
            part = "proof"
        else:
            part = "context"
        structure.append({"part": part, "text": s})

    words = _word_tokens(caption)
    if words & _URGENCY_WORDS:
        angle = "urgency-scarcity"
    elif _OFFER_RE.search(caption):
        angle = "value-offer"
    elif words & _PROOF_WORDS:
        angle = "social-proof"
    elif words & {"dream", "imagine", "finally", "deserve"}:
        angle = "aspiration"
    else:
        angle = "showcase"

    return {
        "hook_line": sentences[0],
        "structure": structure,
        "emotional_angle": angle,
        "cta": cta_text,
        "visual_pattern": ", ".join(visual_tags or []) or None,
    }


def best_pattern(
    tenant_id: str,
    *,
    artist: str | None = None,
    dsn: str | None = None,
) -> dict[str, Any] | None:
    """The top-scoring competitor post + its deconstruction, or ``None`` when no
    competitor data is on file (honest-empty — the caller states it, never fakes
    a pattern). One optional policy-clamped LLM refinement of the ABSTRACT parts
    of the deconstruction runs only when ``ANTHROPIC_API_KEY`` is armed; the
    deterministic deconstruction is the fallback and always present."""
    scored = score_posts(tenant_id, artist=artist, dsn=dsn)
    if not scored:
        return None
    top = scored[0]
    pattern: dict[str, Any] = {
        "post_id": top["id"],
        "handle": top.get("handle"),
        "url": top.get("url"),
        "platform": top.get("platform"),
        "total_score": top.get("total_score"),
        "scores": top.get("scores") or {},
        "why_it_worked": top.get("why_it_worked"),
        "llm_refined": False,
    }
    pattern.update(
        deconstruct_caption(top.get("caption") or "", top.get("visual_tags") or [])
    )

    # Optional single clamped LLM read — refines the ABSTRACT labels only (angle +
    # a structure note); the verbatim fields (hook_line/cta) stay deterministic
    # evidence. Keyless/failed runs skip honestly (llm_refined stays False).
    if os.environ.get("ANTHROPIC_API_KEY") and (top.get("caption") or "").strip():
        try:
            from pydantic import BaseModel

            from cells.base import Cell

            class _Refined(BaseModel):
                emotional_angle: str
                structure_note: str

            cell = Cell(
                name="competitor_pattern",
                schema=_Refined,
                instructions=(
                    "You are deconstructing a competitor's social post into an "
                    "ABSTRACT, reusable pattern. Describe SHAPE only (emotional "
                    "angle label + one sentence on the structure). Judge ONLY the "
                    "provided text; never rewrite, quote, or extend the "
                    "competitor's sentences."
                ),
            )  # default model stays under the 8sk clamp
            got = cell.run_sync(
                f"CAPTION: {(top.get('caption') or '')[:1200]}\n"
                f"DETERMINISTIC READ: angle={pattern.get('emotional_angle')}, "
                f"structure={[s['part'] for s in pattern.get('structure') or []]}"
            )
            pattern["emotional_angle"] = got.emotional_angle.strip()[:80]
            pattern["structure_note"] = got.structure_note.strip()[:300]
            pattern["llm_refined"] = True
        except Exception as exc:  # honest degradation — deterministic read stands
            pattern["llm_error"] = type(exc).__name__
    return pattern


def render_competitor_pattern_block(pattern: dict[str, Any] | None) -> str:
    """The brief block ordering the drafter to MOLD the pattern — with the source
    url + full score breakdown as traceable evidence — or the honest empty
    statement when no competitor data exists."""
    if not pattern:
        return (
            "\nCOMPETITOR CREATIVE INTELLIGENCE: no competitor posts on file — "
            "nothing to mold from. Do NOT invent any 'competitors are doing X' "
            "claim; draft from the artist memory, proven brand patterns, and "
            "cited research only."
        )
    scores = pattern.get("scores") or {}
    breakdown = ", ".join(
        f"{k} {v:g}" if v is not None else f"{k} no-data(excluded)"
        for k, v in scores.items()
    )
    lines = [
        "\nCOMPETITOR CREATIVE INTELLIGENCE — best-scoring competitor post "
        "(operator-provided data). This is INSPIRATION TO MOLD, not material to "
        "reuse: take the structure/hook-shape/CTA-shape from this pattern; "
        "artwork ONLY from our library; wording in OUR brand voice; offers ONLY "
        "substantiated codes; NEVER copy competitor sentences verbatim.",
        f"  - source: @{pattern.get('handle')}"
        + (f" ({pattern.get('platform')})" if pattern.get("platform") else "")
        + (f" — {pattern.get('url')}" if pattern.get("url") else " — (no url provided)"),
        f"  - score: {pattern.get('total_score')}/10 — breakdown: {breakdown or '(none)'}",
    ]
    if pattern.get("hook_line"):
        lines.append(
            f"  - hook line (shape reference ONLY, do not reuse the words): "
            f"\"{pattern['hook_line'][:160]}\""
        )
    parts = [s.get("part") for s in pattern.get("structure") or []]
    if parts:
        lines.append(f"  - structure: {' -> '.join(parts)}")
    if pattern.get("structure_note"):
        lines.append(f"  - structure note: {pattern['structure_note']}")
    if pattern.get("emotional_angle"):
        lines.append(f"  - emotional angle: {pattern['emotional_angle']}")
    if pattern.get("cta"):
        lines.append(
            f"  - CTA shape (reference ONLY, substitute OUR offer/booking path): "
            f"\"{pattern['cta'][:160]}\""
        )
    if pattern.get("visual_pattern"):
        lines.append(f"  - visual pattern: {pattern['visual_pattern']}")
    if pattern.get("why_it_worked"):
        lines.append(f"  - why it worked: {pattern['why_it_worked']}")
    return "\n".join(lines)
