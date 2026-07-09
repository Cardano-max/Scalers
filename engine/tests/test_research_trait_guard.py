"""protected_traits — the deterministic sensitive-trait guard (spec §7/§24).

Pins the compliance-critical ban: no research surface may assert a protected trait
(gender, age, ethnicity/race, health/disability, religion, sexuality, financial
status/distress, immigration status, political views) about a lead unless the
customer's OWN first-party data provides it. Adversarial fixtures included — the
canonical one is "she is probably a young latina woman who can't afford it".

All deterministic: no model, no DB, no network (the provider is a stub).
"""

from __future__ import annotations

from types import SimpleNamespace

from research.protected_traits import (
    AGE,
    ETHNICITY,
    FINANCIAL_STATUS,
    GENDER,
    HEALTH,
    IMMIGRATION_STATUS,
    POLITICAL_VIEWS,
    PROTECTED_TRAITS,
    RELIGION,
    SEXUALITY,
    allowed_categories,
    build_first_party_corpus,
    filter_lines,
    scan_protected_traits,
    trait_violations,
)
from studio.customer_research import _research_query, research_studio

ADVERSARIAL = "she is probably a young latina woman who can't afford it"


# --------------------------------------------------------------------------- #
# Registry + scanner
# --------------------------------------------------------------------------- #
def test_registry_covers_every_spec_category():
    assert set(PROTECTED_TRAITS) == {
        GENDER, AGE, ETHNICITY, HEALTH, RELIGION, SEXUALITY,
        FINANCIAL_STATUS, IMMIGRATION_STATUS, POLITICAL_VIEWS,
    }
    for cat, patterns in PROTECTED_TRAITS.items():
        assert patterns, f"category {cat} has no detection patterns"


def test_scanner_catches_the_canonical_adversarial_line():
    cats = {m.category for m in scan_protected_traits(ADVERSARIAL)}
    assert {GENDER, AGE, ETHNICITY, FINANCIAL_STATUS} <= cats


def test_scanner_catches_each_category():
    samples = {
        GENDER: "he's a regular here",
        AGE: "an elderly client in her sixties",
        ETHNICITY: "a hispanic man from the neighborhood",
        HEALTH: "she was diagnosed with anxiety",
        RELIGION: "a devout catholic",
        SEXUALITY: "openly queer",
        FINANCIAL_STATUS: "living paycheck to paycheck",
        IMMIGRATION_STATUS: "an undocumented immigrant",
        POLITICAL_VIEWS: "votes for republicans",
    }
    for cat, text in samples.items():
        assert cat in {m.category for m in scan_protected_traits(text)}, (cat, text)


def test_craft_vocabulary_scans_clean():
    # Ordinary tattoo-domain language must NOT read as a protected trait.
    for text in (
        "old-school traditional flash with bold lines",
        "old school americana sleeve",
        "japanese-style irezumi sleeve, black and grey shading",
        "fine-line floral piece on the wrist",
        "blackwork geometric pattern",
        "asian-inspired dragon design",
        "she asked about pricing",  # bare pronoun without a copula is not an assertion
    ):
        assert scan_protected_traits(text) == [], text


# --------------------------------------------------------------------------- #
# The two honest carve-outs
# --------------------------------------------------------------------------- #
def test_dob_permits_age_derivation_only():
    assert allowed_categories({"dob": "1999-04-02"}) == frozenset({AGE})
    assert allowed_categories({"name": "Sarah"}) == frozenset()
    assert allowed_categories(None) == frozenset()

    text = "age group 25-34"
    assert trait_violations(text, allowed=allowed_categories({"dob": "1999-04-02"})) == []
    viols = trait_violations(text, allowed=allowed_categories({"name": "Sarah"}))
    assert [v.category for v in viols] == [AGE]


def test_customers_own_words_are_exempt_but_inference_is_not():
    corpus = build_first_party_corpus(
        {"name": "Sarah Kim"},
        [{"speaker": "customer", "text": "I love it but I can't afford it right now."}],
    )
    # Quoting the customer's own stated words: allowed (stated, not inferred).
    assert trait_violations("can't afford it right now", first_party_corpus=corpus) == []
    # The model's third-person financial characterization: still banned.
    assert trait_violations("she is broke", first_party_corpus=corpus)


def test_persona_and_social_never_launder_a_trait_into_the_exemption_corpus():
    corpus = build_first_party_corpus(
        {"name": "Sam", "persona_traits": {"gender": "woman", "age_group": "young"}}
    )
    assert trait_violations("a young woman", first_party_corpus=corpus)


# --------------------------------------------------------------------------- #
# Line filter
# --------------------------------------------------------------------------- #
def test_filter_lines_drops_only_offending_lines_and_records_the_drop():
    text = "Fine-line florals and script.\n" + ADVERSARIAL + "\nBooks open for spring."
    clean, drops = filter_lines(text)
    assert "Fine-line florals" in clean
    assert "Books open for spring." in clean
    assert "latina" not in clean.lower()
    assert len(drops) == 1
    assert GENDER in drops[0]["categories"]
    assert ETHNICITY in drops[0]["categories"]
    # The record carries the short matched tokens, never the full asserted sentence.
    assert "probably a young latina woman" not in drops[0]["matched"]


def test_filter_lines_fully_offending_text_comes_back_empty():
    clean, drops = filter_lines(ADVERSARIAL)
    assert clean == ""
    assert drops


# --------------------------------------------------------------------------- #
# research_studio applies the filter before hits are returned/persisted
# --------------------------------------------------------------------------- #
class _Provider:
    enabled = True

    def __init__(self, hits):
        self._hits = hits
        self.queries: list[str] = []

    def search(self, query, *, limit=5):
        self.queries.append(query)
        return self._hits


def _hit(url, title=None, snippet=None):
    return SimpleNamespace(url=url, title=title, snippet=snippet)


def _consumer_facts(**kw):
    base = {
        "customer_id": "cust_1", "name": "Sarah Kim", "city": "Austin",
        "interests": ["fine-line"], "persona_traits": {}, "tattoo_history": [],
        "memories": [], "customer_type": None,
    }
    base.update(kw)
    return base


def test_research_studio_scrubs_trait_asserting_snippets(monkeypatch):
    provider = _Provider([
        _hit("https://sarahkim.example.com",
             title="Sarah Kim — fine-line tattoo collector",
             snippet="Sarah Kim is a young latina woman who collects fine-line work."),
        _hit("https://instagram.com/sarahk",
             title=None,
             snippet="she is probably in her twenties"),
        _hit("https://blog.example.com/flash",
             title="Old school traditional flash day",
             snippet="Walk-ins welcome for old-school americana."),
    ])
    monkeypatch.setattr(
        "research.pipeline.live_registry", lambda *a, **k: {"firecrawl": provider}
    )
    hits = research_studio(_consumer_facts(), enabled=True)

    by_url = {h["url"]: h for h in hits}
    # Hit 1: offending snippet blanked, clean title kept, scrub recorded honestly.
    kept = by_url["https://sarahkim.example.com"]
    assert kept["snippet"] is None
    assert kept["title"] == "Sarah Kim — fine-line tattoo collector"
    assert any(n.startswith("snippet:") for n in kept["trait_filtered"])
    # Hit 2: nothing citable survived -> the whole hit is dropped.
    assert "https://instagram.com/sarahk" not in by_url
    # Hit 3: craft vocabulary untouched, no scrub note.
    clean = by_url["https://blog.example.com/flash"]
    assert clean["snippet"] == "Walk-ins welcome for old-school americana."
    assert "trait_filtered" not in clean


def test_research_studio_consumer_vs_studio_query_shape(monkeypatch):
    provider = _Provider([])
    monkeypatch.setattr(
        "research.pipeline.live_registry", lambda *a, **k: {"firecrawl": provider}
    )
    # Consumer lead (skindesign shape: blank customer_type) -> person-shaped query.
    research_studio(_consumer_facts(), enabled=True)
    assert provider.queries[-1] == '"Sarah Kim" Austin fine-line tattoo'
    assert "studio" not in provider.queries[-1]
    # Explicit studio lead -> the business-shaped query stays.
    research_studio(
        _consumer_facts(name="Ink Haven", city="Portland", customer_type="studio",
                        interests=[]),
        enabled=True,
    )
    assert provider.queries[-1] == '"Ink Haven" Portland tattoo studio'


def test_research_query_is_pure_and_honest_empty_fields_drop_out():
    assert _research_query({"name": "Sam Lee"}) == '"Sam Lee" tattoo'
    assert _research_query(
        {"name": "Sam Lee", "city": "Reno", "customer_type": "tattoo shop"}
    ) == '"Sam Lee" Reno tattoo studio'
