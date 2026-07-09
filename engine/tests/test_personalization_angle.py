"""Per-lead personalization (#3) + research diversity (#10) — hermetic, DB-free.

The operator's complaint was that a 10-lead campaign produced ten near-identical
"Hello from one studio to another" emails. These tests pin the fix at the source:

* two leads with DIFFERENT real data produce materially DIFFERENT draft angles, each
  carrying an explicit "why this draft is different from the others" rationale;
* a thin-data lead is HONESTLY flagged generic (not faked);
* per-lead web research collects DISTINCT source TYPES (website / social / listing)
  and binds each source to THAT lead — and degrades to one source honestly.

All hermetic: the deterministic copy path (``SCALERS_OUTREACH_LLM=0``) needs no model
and no DB, and research is exercised against a fake in-memory provider.
"""

from __future__ import annotations


from studio.customer_research import (
    _choose_angle,
    _classify_source,
    build_outreach_draft,
    research_studio,
)


def _lead(**over):
    base = {
        "customer_id": "cust_x",
        "name": "Ink & Iris Studio",
        "email": "hello@inkiris.example",
        "email_opt_in": True,
        "city": "Austin",
        "persona_traits": {},
        "interests": [],
        "tattoo_history": [],
    }
    base.update(over)
    return base


# ── distinct angle per lead ─────────────────────────────────────────────────── #


def test_two_leads_with_different_data_get_different_angles(monkeypatch):
    monkeypatch.setenv("SCALERS_OUTREACH_LLM", "0")
    # Lead A: real past-work in our history -> a "past-work" angle.
    a = _lead(customer_id="cust_a", name="Rae Studio",
              tattoo_history=[{"style": "fine-line"}])
    # Lead B: a verified research signal about THEIR own positioning -> stronger angle.
    b = _lead(customer_id="cust_b", name="Bold Crow Tattoo", city="Denver")
    research_b = [{
        "url": "https://boldcrow.example/about", "title": "Bold Crow Tattoo",
        "snippet": "Denver's blackwork and neo-traditional specialists.",
        "source_type": "website", "customer_id": "cust_b",
    }]

    da = build_outreach_draft(a, goal="open a conversation", channel="gmail")
    db = build_outreach_draft(b, goal="open a conversation", channel="gmail",
                              research=research_b)

    # Different angle keys, different rationales, materially different copy + subject.
    assert da["angle_key"] == "past-work"
    assert db["angle_key"] == "their-positioning"
    assert da["why_different"] != db["why_different"]
    assert da["draft"] != db["draft"]
    assert da["subject"] != db["subject"]
    # Each rationale names the real basis it stands on (no fabrication).
    assert "fine-line" in da["why_different"]
    assert "boldcrow.example" in db["why_different"]
    # Neither is the old swapped-name "Hello from one studio to another" subject.
    assert da["subject"] != "Hello from one studio to another, Rae"


def test_inferred_persona_angle_is_marked_inferred(monkeypatch):
    monkeypatch.setenv("SCALERS_OUTREACH_LLM", "0")
    lead = _lead(persona_traits={"aesthetic_lean": "neo-traditional"})
    draft = build_outreach_draft(lead, goal="say hi", channel="gmail")
    assert draft["inferred"] is True
    assert draft["generic"] is False
    assert "inferred" in draft["why_different"].lower()
    assert any(g == "personalization=inferred" for g in draft["grounding"])


def test_thin_data_lead_is_honestly_generic_not_faked(monkeypatch):
    monkeypatch.setenv("SCALERS_OUTREACH_LLM", "0")
    # Name only — no city, no history, no interests, no research.
    lead = _lead(name="Anon Studio", city=None)
    draft = build_outreach_draft(lead, goal="introduce ourselves", channel="gmail")
    assert draft["generic"] is True
    assert draft["angle_key"] == "generic"
    assert "honest-generic" in draft["why_different"].lower() or "generic" in draft["angle_key"]
    assert any(g == "personalization=generic-honest" for g in draft["grounding"])


def test_angle_prefers_real_research_over_persona():
    # Research positioning is the strongest differentiator and wins over persona lean.
    facts = _lead(persona_traits={"aesthetic_lean": "color"},
                  tattoo_history=[{"style": "florals"}])
    research = [{"url": "https://x.example/p", "title": "X", "snippet": "award-winning script work"}]
    angle = _choose_angle(facts, research)
    assert angle["key"] == "their-positioning"
    assert angle["inferred"] is False
    # Without research, it falls to the next real signal (past-work), not generic.
    assert _choose_angle(facts, [])["key"] == "past-work"


# ── research diversity + binding ────────────────────────────────────────────── #


class _Hit:
    def __init__(self, url, title=None, snippet=None):
        self.url = url
        self.title = title
        self.snippet = snippet


class _Provider:
    enabled = True

    def __init__(self, hits):
        self._hits = hits

    def search(self, query, limit=6):
        return self._hits[:limit]


def _patch_provider(monkeypatch, hits):
    import research.pipeline as rp

    monkeypatch.setattr(rp, "live_registry", lambda: {"firecrawl": _Provider(hits)})


def test_classify_source_by_host():
    assert _classify_source("https://www.instagram.com/studio") == "social"
    assert _classify_source("https://www.yelp.com/biz/studio") == "listing"
    assert _classify_source("https://inkiris.example/about") == "website"


def test_research_diversifies_source_types_and_binds_lead(monkeypatch):
    # Provider returns three website hits then a social + a listing; the diversifier
    # must surface DISTINCT types (website, social, listing), each bound to the lead.
    hits = [
        _Hit("https://inkiris.example/", "Home", "official site"),
        _Hit("https://inkiris.example/gallery", "Gallery", "more work"),
        _Hit("https://www.instagram.com/inkiris", "IG", "daily flash"),
        _Hit("https://www.yelp.com/biz/inkiris", "Yelp", "4.8 stars"),
    ]
    _patch_provider(monkeypatch, hits)
    out = research_studio(_lead(customer_id="cust_z"), enabled=True)
    types = [s["source_type"] for s in out]
    assert types == ["website", "social", "listing"]  # distinct, website-first
    assert all(s["customer_id"] == "cust_z" for s in out)  # bound to THIS lead
    # Verbatim only — nothing fabricated.
    assert out[0]["url"] == "https://inkiris.example/"


def test_research_honest_single_source_no_padding(monkeypatch):
    _patch_provider(monkeypatch, [_Hit("https://only.example/", "Only", "one hit")])
    out = research_studio(_lead(), enabled=True)
    assert len(out) == 1
    assert out[0]["source_type"] == "website"


def test_research_disabled_returns_empty(monkeypatch):
    _patch_provider(monkeypatch, [_Hit("https://x.example/", "X", "y")])
    assert research_studio(_lead(), enabled=False) == []


# ── critic confidence lands on the draft (conf=None fix) ────────────────────── #


def test_draft_quality_conf_varies_by_verdict_and_is_honest_on_error():
    from studio.agui import _draft_quality_conf

    # A confidently-approved (well-grounded) draft lands HIGH; a draft the critic is
    # confident needs revision / rejects lands LOW — real, varying confidence.
    approve = _draft_quality_conf("approve", 0.9)
    revise = _draft_quality_conf("revise", 0.9)
    reject = _draft_quality_conf("reject", 0.9)
    assert approve > revise > reject
    assert approve >= 0.9 and reject <= 0.1
    # A critic that could not judge -> honest unknown (None), never a fabricated score.
    assert _draft_quality_conf("error", 0.0) is None
    assert _draft_quality_conf(None, None) is None
