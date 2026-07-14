"""Identity Guardian tests — "never personalize from a stranger" (client
direction: the researcher must be SURE the found profile is OUR customer).

Pure/deterministic scenario coverage plus the enrich_lead integration gate:
uncertain candidates are surfaced but never written to the dossier memory.
"""

from __future__ import annotations

from studio.identity_guardian import partition_verified, score_identity_match

MAYA = {
    "name": "Maya Torres",
    "email": "maya.torres@example.com",
    "phone": "+1 555 0101",
    "ig_handle": "maya.ink",
    "city": "Austin",
    "interests": ["fine-line", "floral"],
}


# ── Scenario B: common name, multiple strangers ───────────────────────────────


def test_name_only_match_is_uncertain_and_flagged():
    stranger = {
        "url": "https://about.me/mayatorres",
        "quote": "Maya Torres — marketing consultant in Chicago",
    }
    out = score_identity_match(MAYA, stranger)
    assert out["verdict"] == "uncertain"
    assert any("name-only" in c for c in out["concerns"])


def test_wrong_instagram_account_is_rejected_outright():
    # The hit IS somebody's profile — just not the handle we have on file.
    imposter = {
        "url": "https://instagram.com/maya_torres_fit",
        "quote": "Maya Torres | fitness coach",
    }
    out = score_identity_match(MAYA, imposter)
    assert out["verdict"] == "rejected"
    assert any("different account" in c for c in out["concerns"])


def test_no_name_match_at_all_is_rejected():
    out = score_identity_match(MAYA, {"url": "https://example.com/blog",
                                      "quote": "Ten tattoo aftercare tips"})
    assert out["verdict"] == "rejected"


# ── hard identifiers confirm ──────────────────────────────────────────────────


def test_ig_handle_on_file_confirms():
    out = score_identity_match(MAYA, {
        "url": "https://instagram.com/maya.ink",
        "quote": "Maya Torres (@maya.ink) — flash and fine line",
    })
    assert out["verdict"] == "confirmed" and out["confidence"] >= 0.85
    assert any("instagram handle" in e for e in out["evidence"])


def test_phone_digits_confirm():
    out = score_identity_match(MAYA, {
        "url": "https://somedirectory.com/listing",
        "quote": "Contact Maya Torres at (555) 010-1 for bookings",
    })
    assert out["verdict"] == "confirmed"


def test_business_domain_confirms_but_freemail_does_not():
    biz = dict(MAYA, email="maya@torresdesign.co")
    out = score_identity_match(biz, {
        "url": "https://torresdesign.co/about",
        "quote": "Maya Torres, founder",
    })
    assert out["verdict"] == "confirmed"
    # A freemail domain (example.com is in the ignore list) must NOT confirm.
    out2 = score_identity_match(MAYA, {
        "url": "https://example.com/somepage",
        "quote": "Maya Torres",
    })
    assert out2["verdict"] != "confirmed"


# ── corroborated name = likely ────────────────────────────────────────────────


def test_name_plus_city_is_likely_and_name_plus_interest_is_likely():
    city = score_identity_match(MAYA, {
        "url": "https://news.example.org/austin-artists",
        "quote": "Austin creative Maya Torres shows her botanical work",
    })
    assert city["verdict"] == "likely"
    interest = score_identity_match(MAYA, {
        "url": "https://portfolio.example.org/mt",
        "quote": "Maya Torres — fine-line specialist",
    })
    # 'fine-line' tokens {fine, line} appear in 'fine-line specialist'
    assert interest["verdict"] == "likely"


# ── partition: only verified flows on ─────────────────────────────────────────


def test_partition_keeps_verified_surfaces_uncertain_drops_rejected():
    cands = [
        {"url": "https://instagram.com/maya.ink", "quote": "@maya.ink flash"},   # confirmed
        {"url": "https://blog.example.org/x", "quote": "Maya Torres in Austin"},  # likely
        {"url": "https://about.me/mayatorres", "quote": "Maya Torres, Chicago"},  # uncertain
        {"url": "https://instagram.com/other_maya", "quote": "Maya T"},           # rejected
    ]
    out = partition_verified(MAYA, cands)
    assert len(out["verified"]) == 2
    assert len(out["unverified"]) == 1
    assert out["rejected_count"] == 1
    assert out["counts"] == {"confirmed": 1, "likely": 1, "uncertain": 1, "rejected": 1}
    assert all("identity" in f for f in out["verified"] + out["unverified"])


# ── enrich_lead integration: unverified facts never reach the dossier ─────────


def test_enrich_lead_writes_only_identity_verified_facts(monkeypatch):
    import studio.lead_enrichment as le

    monkeypatch.setattr(le, "lookup_lead", lambda *a, **k: MAYA | {"id": "cust_m"},
                        raising=False)
    # Route lookup_lead import inside enrich_lead:
    import studio.customer_research as cr
    monkeypatch.setattr(cr, "lookup_lead", lambda *a, **k: dict(MAYA, id="cust_m"))

    hits = [
        {"quote": "Maya Torres (@maya.ink) botanical flash", "url": "https://instagram.com/maya.ink",
         "query": "q1", "angle": "person", "source_type": "social", "login_walled": True},
        {"quote": "Maya Torres, Chicago marketing consultant", "url": "https://about.me/mayatorres",
         "query": "q1", "angle": "person", "source_type": "website", "login_walled": False},
    ]
    monkeypatch.setattr(le, "_collect_cited_facts", lambda queries, **k: (hits, []))

    written: dict = {}

    def fake_write(tenant_id, facts, kept, dsn=None):
        written["facts"] = kept
        return "mem_test"

    monkeypatch.setattr(le, "_write_enrichment_memory", fake_write)
    out = le.enrich_lead("t_x", "cust_m")
    # Only the handle-confirmed fact was written; the Chicago stranger was not.
    assert len(out["found"]) == 1 and out["found"][0]["identity"]["verdict"] == "confirmed"
    assert len(out["unverified"]) == 1
    assert written["facts"] == out["found"]
    assert out["identity_counts"]["uncertain"] == 1


# ── the copywriter's research feed is guardian-gated (stranger-leak fix) ──────


def test_identity_verified_research_drops_stranger_hits(monkeypatch):
    """A real prod run surfaced an actress's Wikipedia page for a common-name
    lead; identity_verified_research must pass only hits the guardian vouches
    for, so a stranger never reaches the angle chooser or the email prompt."""
    import studio.customer_research as cr

    hits = [
        {"url": "https://instagram.com/maya.ink", "snippet": "@maya.ink flash",
         "title": "IG", "source_type": "social", "customer_id": "cust_m"},
        {"url": "https://en.wikipedia.org/wiki/Maya_Torres",
         "snippet": "Maya Torres is an actress", "title": "Wikipedia",
         "source_type": "website", "customer_id": "cust_m"},
    ]
    monkeypatch.setattr(cr, "research_studio", lambda facts, *, enabled: hits)
    out = cr.identity_verified_research(MAYA | {"customer_id": "cust_m"}, enabled=True)
    assert [h["url"] for h in out] == ["https://instagram.com/maya.ink"]
    assert out[0]["identity"]["verdict"] == "confirmed"
