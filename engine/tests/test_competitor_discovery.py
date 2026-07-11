"""LIVE competitor discovery (studio/competitor_discovery.py) — all HTTP mocked.

ToS-compliance is the product rule under test: the ONLY two data paths are
Firecrawl public-web search (candidate handles parsed from real result
urls/snippets) and Meta's OFFICIAL Business Discovery Graph API (one GET per
handle, operator credentials, token in the Authorization header NEVER the URL,
``appsecret_proof`` signed). No test touches the network (this sandbox blocks
graph.facebook.com anyway) — the seams are the injectable ``search``/``fetch``
callables and the module-level ``_urlopen`` indirection.

Pure parts: handle extraction (instagram.com urls, @mentions, reserved-segment
and domain/email guards, dedupe, own-handle drop), query construction,
niche/city resolution from the tenant pack / plan goal, the Graph request
SHAPE, honest per-handle misses (OAuth non-business account), missing-creds
degradation, and the time budget.

Postgres integration (skipif no ENGINE_DATABASE_URL): run_discovery end-to-end
with fake providers — rows persisted with ``source='discovery'`` + verbatim
captions + real-only metrics, scored by the EXISTING scorer, idempotent
re-runs; and the gate wiring (empty table + competitor_research → discovery →
pause with ``source='discovery'`` options; failure → the honest skip note).
"""

from __future__ import annotations

import hashlib
import hmac
import io
import json
import os
import urllib.error
import uuid
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

from research.providers._http import SearchResult

_pg = pytest.mark.skipif(
    not os.environ.get("ENGINE_DATABASE_URL"),
    reason="requires Postgres (set ENGINE_DATABASE_URL)",
)

DSN = os.environ.get(
    "ENGINE_DATABASE_URL", "postgresql://scalers:scalers@localhost:5432/scalers"
)


def _cleanup(tenant: str, run_ids: list[str] | None = None) -> None:
    import psycopg

    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute("DELETE FROM competitor_posts WHERE tenant_id=%s", (tenant,))
        for rid in run_ids or []:
            c.execute("DELETE FROM competitor_selections WHERE run_id=%s", (rid,))
            c.execute("DELETE FROM agent_runs WHERE run_id=%s", (rid,))
            c.execute("DELETE FROM runs WHERE run_id=%s", (rid,))


# --------------------------------------------------------------------------- #
# Shared fakes — Firecrawl-shaped hits + Business-Discovery-shaped profiles.
# --------------------------------------------------------------------------- #
def _hits() -> list[SearchResult]:
    return [
        SearchResult(
            url="https://www.instagram.com/rivalonelv/",
            title="Rival One LV (@rivalonelv) • Instagram",
            snippet=None,
        ),
        SearchResult(
            url="https://vegasmag.example.com/best-tattoo-studios",
            title="Best tattoo studios in Las Vegas",
            snippet=(
                "Our picks: @rivaltwoink and @rivalonelv stand out this year. "
                "Tips: tips@vegasmag.example.com"
            ),
        ),
    ]


def _fake_search(query: str, *, limit: int = 5) -> list[SearchResult]:
    return _hits()


_CAPTION_ONE = "Would you sit 6 hours for this? DM us 'PEONY' today — link in bio."


def _profile_one() -> dict:
    return {
        "username": "rivalonelv",
        "followers_count": 20000,
        "media_count": 231,
        "media": [
            {
                "id": "m1", "caption": _CAPTION_ONE,
                "like_count": 1800, "comments_count": 240, "media_type": "IMAGE",
                "permalink": "https://www.instagram.com/p/AAA111/",
                "timestamp": "2026-07-01T12:00:00+0000",
            },
            {
                "id": "m2", "caption": "Healed fine-line peony.",
                "like_count": 900, "comments_count": 60, "media_type": "IMAGE",
                "permalink": "https://www.instagram.com/p/BBB222/",
                "timestamp": "2026-06-20T12:00:00+0000",
            },
        ],
    }


def _graph_body() -> bytes:
    """The RAW Graph response shape (media nested under ``{"data": [...]}``)."""
    p = _profile_one()
    return json.dumps(
        {"business_discovery": {**p, "media": {"data": p["media"]}}}
    ).encode()


def _fake_fetch(handle: str) -> dict:
    from studio.competitor_discovery import BusinessDiscoveryError

    if handle == "rivalonelv":
        return _profile_one()
    # The REAL shape of a Business Discovery miss: an OAuth error per handle.
    raise BusinessDiscoveryError(
        f"@{handle}: HTTP 400: (#110) The requested user is not an Instagram "
        "Business account — OAuthException — code 110"
    )


_PLAN = SimpleNamespace(goal="book fine-line tattoo clients in Las Vegas", artist=None)


# --------------------------------------------------------------------------- #
# Pure: handle extraction from Firecrawl-shaped results.
# --------------------------------------------------------------------------- #
def test_extract_handles_urls_mentions_dedupe_and_own_handle_dropped():
    from studio.competitor_discovery import extract_handles

    out = extract_handles(_hits(), own_handles={"skindesign"}, limit=10)
    # First-seen order: the profile url, then the @mention; @rivalonelv deduped;
    # the email local/domain never becomes a handle.
    assert [c["handle"] for c in out] == ["rivalonelv", "rivaltwoink"]
    assert out[0]["evidence_url"] == "https://www.instagram.com/rivalonelv/"
    assert out[0]["evidence_title"] == "Rival One LV (@rivalonelv) • Instagram"
    assert out[1]["evidence_url"] == "https://vegasmag.example.com/best-tattoo-studios"

    # The tenant's OWN handle is dropped wherever it appears.
    own = extract_handles(
        [SearchResult(url="https://instagram.com/SkinDesign", title="us", snippet="@skindesign")],
        own_handles={"skindesign"},
    )
    assert own == []


def test_extract_handles_reserved_segments_domains_and_limit():
    from studio.competitor_discovery import extract_handles

    hits = [
        # Product routes are NOT handles.
        SearchResult(url="https://www.instagram.com/reel/XYZ123/", title=None, snippet=None),
        SearchResult(url="https://www.instagram.com/explore/tags/tattoo/", title=None, snippet=None),
        # An @mention that is really a domain is dropped.
        SearchResult(url="https://x.example.com", title=None, snippet="visit @studio.com now"),
        # Email local parts never match (lookbehind).
        SearchResult(url="https://y.example.com", title=None, snippet="mail info@realstudio"),
        SearchResult(url="https://www.instagram.com/ink_habit.lv", title="Ink Habit", snippet=None),
        SearchResult(url="https://www.instagram.com/secondstudio/", title=None, snippet=None),
    ]
    out = extract_handles(hits, limit=1)
    assert [c["handle"] for c in out] == ["ink_habit.lv"]  # capped at limit
    out_all = extract_handles(hits, limit=10)
    assert [c["handle"] for c in out_all] == ["ink_habit.lv", "secondstudio"]


def test_discover_candidate_handles_builds_niche_city_queries_and_dedupes():
    from studio.competitor_discovery import discover_candidate_handles

    queries: list[str] = []

    def searcher(q: str, *, limit: int = 5):
        queries.append(q)
        return _hits()

    out = discover_candidate_handles(
        "skindesign",
        niche_terms=["fine-line", "tattoo"],
        city="Las Vegas",
        limit=10,
        search=searcher,
    )
    assert queries == [
        "best fine-line tattoo Las Vegas instagram",
        "fine-line tattoo Las Vegas top studios",
        "top fine-line tattoo accounts instagram Las Vegas",
    ]
    # Three queries returned the same hits — still exactly two deduped handles.
    assert [c["handle"] for c in out] == ["rivalonelv", "rivaltwoink"]


def test_discover_candidate_handles_honest_empty_without_key_or_terms():
    from studio.competitor_discovery import discover_candidate_handles

    # No FIRECRAWL_API_KEY (empty env) and no injected search → NO live call,
    # honest-empty. Never a fabricated handle.
    assert discover_candidate_handles(
        "t", niche_terms=["tattoo"], city="Las Vegas", env={}
    ) == []
    # No niche terms → nothing to search for.
    assert discover_candidate_handles(
        "t", niche_terms=[], city="Las Vegas", search=_fake_search
    ) == []

    def boom(q, *, limit=5):
        raise RuntimeError("search down")

    # Every query failing degrades to honest-empty, never raises.
    assert discover_candidate_handles(
        "t", niche_terms=["tattoo"], city="", search=boom
    ) == []


# --------------------------------------------------------------------------- #
# Pure: niche/city grounding — pack positioning → plan goal → display name.
# --------------------------------------------------------------------------- #
def test_resolve_niche_city_from_real_pack_positioning():
    from studio.competitor_discovery import resolve_niche_city

    # ladies8391's REAL pack: "a woman-owned Austin studio specializing in
    # neo-traditional color and fine-line tattoos".
    terms, city, evidence = resolve_niche_city("ladies8391")
    assert city == "Austin"
    assert evidence == "tenant pack positioning"
    assert "tattoos" in terms and "fine-line" in terms
    assert "austin" not in terms  # the city is not repeated in the niche terms


def test_resolve_niche_city_fallbacks_topic_goal_and_none():
    from studio.competitor_discovery import resolve_niche_city

    ghost = "test_nopack_" + uuid.uuid4().hex[:6]
    # Explicit operator topic wins.
    terms, _city, evidence = resolve_niche_city(ghost, topic="blackwork tattoo reno")
    assert terms == ["blackwork", "tattoo", "reno"] and evidence == "operator topic"
    # No pack → plan goal keywords (stopwords stripped).
    terms2, city2, evidence2 = resolve_niche_city(ghost, plan=_PLAN)
    assert evidence2 == "plan goal keywords"
    assert "tattoo" in terms2 and "vegas" in terms2
    assert city2 == ""  # no positioning on file — never an invented city
    # Nothing anywhere → honest-empty.
    terms3, city3, evidence3 = resolve_niche_city(ghost)
    assert (terms3, city3, evidence3) == ([], "", "none")


# --------------------------------------------------------------------------- #
# Pure: the ONE Graph GET — request SHAPE, parse, honest errors, scrubbing.
# --------------------------------------------------------------------------- #
class _Resp:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_business_discovery_request_shape_token_in_header_never_url(monkeypatch):
    import studio.competitor_discovery as cd

    captured: dict = {}

    def fake_urlopen(req, timeout=0):
        captured["req"], captured["timeout"] = req, timeout
        return _Resp(_graph_body())

    monkeypatch.setattr(cd, "_urlopen", fake_urlopen)
    prof = cd.business_discovery(
        "@rivalonelv", ig_user_id="17841400000000001",
        page_token="tok_secret", app_secret="app_sec",
    )

    req = captured["req"]
    url = req.full_url
    # ONE GET to the official Graph host, versioned, on the OPERATOR's ig id.
    assert req.get_method() == "GET"
    assert url.startswith("https://graph.facebook.com/v25.0/17841400000000001?")
    # The token rides the Authorization header — NEVER the URL.
    assert "tok_secret" not in url
    assert req.get_header("Authorization") == "Bearer tok_secret"
    qs = parse_qs(urlparse(url).query)
    # appsecret_proof = HMAC-SHA256(app_secret, token) as a query param.
    assert qs["appsecret_proof"] == [
        hmac.new(b"app_sec", b"tok_secret", hashlib.sha256).hexdigest()
    ]
    # The exact Business Discovery fields expression (@ stripped from the handle).
    assert qs["fields"] == [
        "business_discovery.username(rivalonelv)"
        "{username,followers_count,media_count,"
        "media.limit(12){id,caption,like_count,comments_count,media_type,"
        "permalink,timestamp}}"
    ]
    # Parsed profile is exactly what the API returned.
    assert prof["username"] == "rivalonelv"
    assert prof["followers_count"] == 20000
    assert prof["media_count"] == 231
    assert [m["id"] for m in prof["media"]] == ["m1", "m2"]
    assert prof["media"][0]["caption"] == _CAPTION_ONE


def test_business_discovery_without_secret_omits_proof(monkeypatch):
    import studio.competitor_discovery as cd

    captured: dict = {}

    def fake_urlopen(req, timeout=0):
        captured["req"] = req
        return _Resp(_graph_body())

    monkeypatch.setattr(cd, "_urlopen", fake_urlopen)
    cd.business_discovery(
        "rivalonelv", ig_user_id="1", page_token="tok_secret", app_secret=None
    )
    qs = parse_qs(urlparse(captured["req"].full_url).query)
    assert "appsecret_proof" not in qs  # never an HMAC over a missing secret


def test_business_discovery_oauth_miss_is_honest_and_token_scrubbed(monkeypatch):
    import studio.competitor_discovery as cd

    # A non-business account: Graph answers with a REAL OAuth error. Token
    # scrubbing is defense in depth — plant it in the body to prove the scrub.
    body = json.dumps({
        "error": {
            "message": "(#110) The requested user is not an Instagram Business "
                       "account tok_secret",
            "type": "OAuthException", "code": 110,
        }
    }).encode()

    def fake_urlopen(req, timeout=0):
        raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", None, io.BytesIO(body))

    monkeypatch.setattr(cd, "_urlopen", fake_urlopen)
    with pytest.raises(cd.BusinessDiscoveryError) as ei:
        cd.business_discovery("notbiz", ig_user_id="1", page_token="tok_secret")
    msg = str(ei.value)
    assert "not an Instagram Business account" in msg  # the REAL Graph error
    assert "HTTP 400" in msg and "code 110" in msg
    assert "tok_secret" not in msg  # token never echoed


def test_business_discovery_missing_node_and_invalid_handle(monkeypatch):
    import studio.competitor_discovery as cd

    calls: list = []

    def fake_urlopen(req, timeout=0):
        calls.append(req)
        return _Resp(json.dumps({"id": "1"}).encode())  # no business_discovery node

    monkeypatch.setattr(cd, "_urlopen", fake_urlopen)
    with pytest.raises(cd.BusinessDiscoveryError, match="no business_discovery node"):
        cd.business_discovery("someone", ig_user_id="1", page_token="tok")

    # A web-parsed string that is not a plausible handle NEVER reaches Graph
    # (the handle is embedded in the fields expression — injection-hardened).
    calls.clear()
    with pytest.raises(cd.BusinessDiscoveryError, match="invalid instagram handle"):
        cd.business_discovery("bad handle){id}", ig_user_id="1", page_token="tok")
    assert calls == []
    with pytest.raises(cd.BusinessDiscoveryError, match="credentials missing"):
        cd.business_discovery("okhandle", ig_user_id="", page_token="")
    assert calls == []


# --------------------------------------------------------------------------- #
# Pure: run_discovery degradations — no creds, time budget, no candidates.
# --------------------------------------------------------------------------- #
def test_run_discovery_without_meta_creds_reports_all_misses():
    from studio.competitor_discovery import run_discovery

    out = run_discovery(
        "test_nocreds_" + uuid.uuid4().hex[:6], plan=_PLAN,
        search=_fake_search, env={},  # candidates found, but no META_* creds
    )
    assert out["ok"] is False
    assert out["candidates"] == 2 and out["fetched"] == 0 and out["posts"] == 0
    assert [m["handle"] for m in out["misses"]] == ["rivalonelv", "rivaltwoink"]
    assert all("credentials not configured" in m["reason"] for m in out["misses"])
    assert "Meta credentials are not configured" in out["note"]


def test_run_discovery_keyless_search_is_honest_empty():
    from studio.competitor_discovery import run_discovery

    out = run_discovery("test_nokey_" + uuid.uuid4().hex[:6], plan=_PLAN, env={})
    assert out["candidates"] == 0 and out["posts"] == 0 and out["ok"] is False
    assert "FIRECRAWL_API_KEY not set" in out["note"]
    # And with NO grounding at all, discovery says so instead of inventing one.
    out2 = run_discovery("test_nothing_" + uuid.uuid4().hex[:6], env={})
    assert "no niche terms resolvable" in out2["note"]


def test_run_discovery_time_budget_bounds_fetches():
    from studio.competitor_discovery import BusinessDiscoveryError, run_discovery

    ticks = iter([0.0, 1.0, 120.0])  # start → handle 1 in budget → handle 2 over

    def fetch(handle: str) -> dict:
        raise BusinessDiscoveryError(f"@{handle}: HTTP 400: token expired")

    out = run_discovery(
        "test_budget_" + uuid.uuid4().hex[:6], plan=_PLAN,
        search=_fake_search, fetch=fetch, env={},
        time_budget_s=60.0, clock=lambda: next(ticks),
    )
    assert out["fetched"] == 0 and out["posts"] == 0
    assert [m["handle"] for m in out["misses"]] == ["rivalonelv", "rivaltwoink"]
    assert "token expired" in out["misses"][0]["reason"]  # the real per-handle miss
    assert "time budget" in out["misses"][1]["reason"]  # bounded, honestly noted
    assert "0 profiles read" in out["note"]


# --------------------------------------------------------------------------- #
# PG: end-to-end — persisted, scored by the EXISTING scorer, idempotent.
# --------------------------------------------------------------------------- #
@_pg
def test_run_discovery_end_to_end_persists_scores_and_is_idempotent():
    import psycopg
    from psycopg.rows import dict_row

    from studio.competitor_discovery import run_discovery

    tenant = "test_disc_e2e_" + uuid.uuid4().hex[:8]
    try:
        out = run_discovery(
            tenant, plan=_PLAN, dsn=DSN,
            search=_fake_search, fetch=_fake_fetch, env={},
        )
        assert out["ok"] is True
        assert out["candidates"] == 2 and out["fetched"] == 1 and out["posts"] == 2
        assert out["note"] == (
            "discovering competitors live: 2 handles found, 1 profiles read, "
            "2 posts scored"
        )
        # The non-business handle is an HONEST miss with the REAL Graph reason.
        assert [m["handle"] for m in out["misses"]] == ["rivaltwoink"]
        assert "not an Instagram Business account" in out["misses"][0]["reason"]
        # Ranked by the EXISTING deterministic scorer.
        assert out["top"] and out["top"][0]["handle"] == "rivalonelv"
        assert out["top"][0]["postId"].startswith("cmp_")
        assert out["top"][0]["totalScore"] is not None
        assert out["top"][0]["whyItWorked"]

        with psycopg.connect(DSN, autocommit=True, row_factory=dict_row) as c:
            rows = c.execute(
                "SELECT * FROM competitor_posts WHERE tenant_id=%s ORDER BY url",
                (tenant,),
            ).fetchall()
        assert len(rows) == 2
        for r in rows:
            assert r["source"] == "discovery"
            assert r["platform"] == "instagram"
            assert r["handle"] == "rivalonelv"
            assert r["total_score"] is not None  # scored + persisted breakdown
            assert r["scores"]
            assert r["posted_at"] is not None
            assert "tattoo" in (r["niche"] or "")
        top_row = rows[0]  # …/p/AAA111/
        assert top_row["caption"] == _CAPTION_ONE  # VERBATIM operator reference
        # metrics carry ONLY what the API returned + the derived rate.
        assert top_row["metrics"] == {
            "likes": 1800, "comments": 240, "followers": 20000,
            "engagement_rate": round((1800 + 240) / 20000, 6),
        }

        # Idempotent re-run: same counts, NO duplicate rows (deterministic ids).
        out2 = run_discovery(
            tenant, plan=_PLAN, dsn=DSN,
            search=_fake_search, fetch=_fake_fetch, env={},
        )
        assert out2["posts"] == 2
        with psycopg.connect(DSN, autocommit=True) as c:
            n = c.execute(
                "SELECT count(*) FROM competitor_posts WHERE tenant_id=%s", (tenant,)
            ).fetchone()[0]
        assert n == 2
    finally:
        _cleanup(tenant)


# --------------------------------------------------------------------------- #
# PG: gate wiring — empty table + competitor_research → discovery → pause.
# --------------------------------------------------------------------------- #
@_pg
def test_gate_runs_live_discovery_then_pauses_with_discovery_source(monkeypatch):
    import studio.competitor_discovery as cd
    from studio.competitor_flow import competitor_gate, get_selection

    tenant = "test_disc_gate_" + uuid.uuid4().hex[:8]
    run_id = f"team-camp_{uuid.uuid4().hex[:8]}-{uuid.uuid4().hex[:8]}"
    real = cd.run_discovery
    seen: dict = {}

    def fake_run_discovery(tenant_id, *, plan=None, dsn=None, **kw):
        seen["tenant"], seen["kw"] = tenant_id, kw
        return real(
            tenant_id, plan=plan, dsn=dsn,
            search=_fake_search, fetch=_fake_fetch, env={},
        )

    monkeypatch.setattr(cd, "run_discovery", fake_run_discovery)
    plan = SimpleNamespace(
        channel_plans={"ig": {"competitor_research": True}},
        artist=None, goal="book fine-line tattoo clients in Las Vegas",
    )
    try:
        state, payload = competitor_gate(run_id, tenant, "sess-disc", plan, dsn=DSN)
        assert state == "pause"
        assert seen["tenant"] == tenant
        assert seen["kw"].get("time_budget_s") == 60.0  # bounded, per the gate
        assert payload["kind"] == "competitor_pick"
        # The honest live-discovery step note rides the pause payload.
        assert payload["note"] == (
            "discovering competitors live: 2 handles found, 1 profiles read, "
            "2 posts scored"
        )
        options = payload["options"]
        assert len(options) == 2
        assert all(o["source"] == "discovery" for o in options)
        assert options[0]["caption"]  # verbatim, for the OPERATOR's review only
        assert options[0]["totalScore"] is not None
        # The pause is durable — restart-safe like the upload path.
        sel = get_selection(run_id, dsn=DSN)
        assert sel is not None and sel["status"] == "awaiting"

        # The awaiting summary surfaces the discovery note as a step note.
        from studio.competitor_flow import awaiting_competitor_summary

        summary = awaiting_competitor_summary(run_id, "camp_x", payload, channel="instagram")
        assert summary["step_notes"][0].startswith("discovering competitors live:")
    finally:
        _cleanup(tenant, [run_id])


@_pg
def test_gate_without_competitor_research_never_discovers(monkeypatch):
    import studio.competitor_discovery as cd
    from studio.competitor_flow import NO_COMPETITOR_NOTE, competitor_gate

    called: list = []
    monkeypatch.setattr(
        cd, "run_discovery", lambda *a, **k: called.append(1) or {}
    )
    tenant = "test_disc_off_" + uuid.uuid4().hex[:8]
    run_id = f"team-camp_{uuid.uuid4().hex[:8]}-{uuid.uuid4().hex[:8]}"
    try:
        # competitor_research NOT set → today's behavior exactly, no discovery.
        plan = SimpleNamespace(channel_plans={"ig": {}}, artist=None, goal="g")
        state, note = competitor_gate(run_id, tenant, "sess-off", plan, dsn=DSN)
        assert (state, note) == ("skip", NO_COMPETITOR_NOTE)
        assert called == []
    finally:
        _cleanup(tenant, [run_id])


@_pg
def test_gate_discovery_failure_degrades_to_honest_skip(monkeypatch):
    import studio.competitor_discovery as cd
    from studio.competitor_flow import NO_COMPETITOR_NOTE, competitor_gate, get_selection

    tenant = "test_disc_fail_" + uuid.uuid4().hex[:8]
    run_id = f"team-camp_{uuid.uuid4().hex[:8]}-{uuid.uuid4().hex[:8]}"
    plan = SimpleNamespace(
        channel_plans={"ig": {"competitor_research": True}}, artist=None, goal="g"
    )
    try:
        # 1) Discovery RAISES → the run continues on the honest skip, named.
        def boom(*a, **k):
            raise RuntimeError("provider down")

        monkeypatch.setattr(cd, "run_discovery", boom)
        state, note = competitor_gate(run_id, tenant, "sess-fail", plan, dsn=DSN)
        assert state == "skip"
        assert NO_COMPETITOR_NOTE in note and "RuntimeError" in note
        assert get_selection(run_id, dsn=DSN) is None  # no unanswerable pause

        # 2) Discovery lands NOTHING (all misses) → skip carries its honest note.
        monkeypatch.setattr(
            cd, "run_discovery",
            lambda *a, **k: {
                "ok": False, "candidates": 3, "fetched": 0, "posts": 0,
                "misses": [], "top": [],
                "note": "discovering competitors live: 3 handles found, "
                        "0 profiles read (3 miss(es)) — nothing stored",
            },
        )
        state2, note2 = competitor_gate(run_id, tenant, "sess-fail", plan, dsn=DSN)
        assert state2 == "skip"
        assert NO_COMPETITOR_NOTE in note2
        assert "0 profiles read" in note2
    finally:
        _cleanup(tenant, [run_id])
