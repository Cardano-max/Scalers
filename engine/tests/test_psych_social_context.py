"""Consent-safe social signals (spec §7): gather_social_context + analyze_customer.

Pins the privacy line: ONLY a customer-provided handle is ever looked up (never
name-based discovery), the single Firecrawl call is gated OFF by default, every
extracted line passes the protected-traits filter, the source URL is cited in the
returned context, and every failure path is honest-None. Network is always mocked.
"""

from __future__ import annotations

from types import SimpleNamespace

from studio.customer_research import gather_social_context
from studio.psych_profile import (
    INSUFFICIENT,
    SRC_SOCIAL,
    STATED,
    PsychField,
    PsychLLMOut,
    analyze_customer,
)


class _Provider:
    def __init__(self, hits=None, *, enabled=True, exc=None):
        self._hits = hits or []
        self.enabled = enabled
        self._exc = exc
        self.queries: list[str] = []

    def search(self, query, *, limit=5):
        self.queries.append(query)
        if self._exc is not None:
            raise self._exc
        return self._hits


def _hit(url, title=None, snippet=None):
    return SimpleNamespace(url=url, title=title, snippet=snippet)


def _registry(monkeypatch, provider):
    monkeypatch.setattr(
        "research.pipeline.live_registry", lambda *a, **k: {"firecrawl": provider}
    )
    return provider


def _facts(**kw):
    base = {
        "customer_id": "cust_1", "name": "Sarah Kim", "city": "Austin",
        "interests": ["fine-line"], "persona_traits": {}, "tattoo_history": [],
        "memories": [],
    }
    base.update(kw)
    return base


# --------------------------------------------------------------------------- #
# gather_social_context — the pure fetcher
# --------------------------------------------------------------------------- #
def test_disabled_or_handleless_makes_no_call(monkeypatch):
    provider = _registry(monkeypatch, _Provider([_hit("https://x", title="t")]))
    # Disabled -> None, zero calls.
    assert gather_social_context(_facts(ig_handle="@sarahk"), enabled=False) is None
    # Enabled but NO customer-provided handle -> None, zero calls: a lead's NAME is
    # never used for social discovery (the privacy line).
    assert gather_social_context(_facts(), enabled=True) is None
    assert provider.queries == []


def test_ig_handle_one_site_search_cited_url(monkeypatch):
    provider = _registry(monkeypatch, _Provider([
        _hit("https://instagram.com/sarahk",
             title="Sarah K (@sarahk)",
             snippet="Fine-line florals. Austin. Books open."),
    ]))
    out = gather_social_context(_facts(ig_handle="@sarahk"), enabled=True)
    assert out is not None
    assert "(source: https://instagram.com/sarahk)" in out
    assert "Fine-line florals" in out
    # Exactly one call, shaped as a site-search for the handle ('@' stripped).
    assert provider.queries == ["site:instagram.com sarahk"]


def test_linkedin_handle_used_when_no_ig(monkeypatch):
    provider = _registry(monkeypatch, _Provider([]))
    gather_social_context(_facts(linkedin_handle="sarah-kim-123"), enabled=True)
    assert provider.queries == ["site:linkedin.com/in sarah-kim-123"]


def test_extracted_text_passes_the_trait_filter(monkeypatch):
    _registry(monkeypatch, _Provider([
        _hit("https://instagram.com/sarahk",
             title="Sarah K (@sarahk)",
             snippet="she/her — young latina tattoo collector"),
    ]))
    out = gather_social_context(_facts(ig_handle="sarahk"), enabled=True)
    # The only text line asserted protected traits -> honest None, not a scrubbed stub.
    assert out is None


def test_network_failure_and_keyless_degrade_to_none(monkeypatch):
    _registry(monkeypatch, _Provider(exc=RuntimeError("proxy blocked host")))
    assert gather_social_context(_facts(ig_handle="sarahk"), enabled=True) is None
    _registry(monkeypatch, _Provider([_hit("https://x", title="t")], enabled=False))
    assert gather_social_context(_facts(ig_handle="sarahk"), enabled=True) is None


# --------------------------------------------------------------------------- #
# analyze_customer wiring — auto-fetch only with a handle + an explicit gate
# --------------------------------------------------------------------------- #
def _spy_gather(monkeypatch, result="Fine-line collector since 2022 (source: https://instagram.com/sarahk)"):
    calls: list[dict] = []

    def fake(facts, enabled=False):
        calls.append({"enabled": enabled, "facts": facts})
        return result

    monkeypatch.setattr("studio.customer_research.gather_social_context", fake)
    return calls


def test_analyst_fetches_social_for_handle_and_grounds_evidence(monkeypatch):
    calls = _spy_gather(monkeypatch)
    monkeypatch.setattr(
        "studio.psych_profile._build_psych_cell",
        lambda: SimpleNamespace(run_sync=lambda prompt: PsychLLMOut(
            intent_strength=PsychField(
                value="moderate", signal=STATED,
                evidence="Fine-line collector since 2022",
                evidence_source=SRC_SOCIAL,
            ),
        )),
    )
    prof = analyze_customer(
        _facts(ig_handle="sarahk"), use_llm=True, fetch_social=True
    )
    assert len(calls) == 1
    # The fetched social text is real corpus: a stated read quoting it SURVIVES,
    # tagged with the social source (whose text carries the cited URL).
    assert prof.intent_strength.value == "moderate"
    assert prof.intent_strength.signal == STATED
    assert prof.intent_strength.evidence_source == SRC_SOCIAL


def test_analyst_default_gate_is_off(monkeypatch):
    calls = _spy_gather(monkeypatch)
    monkeypatch.delenv("STUDIO_SOCIAL_RESEARCH", raising=False)
    monkeypatch.delenv("STUDIO_DEEP_RESEARCH", raising=False)
    analyze_customer(_facts(ig_handle="sarahk"), use_llm=False)
    assert calls == []  # no opt-in -> no live egress


def test_analyst_env_opt_in_enables_fetch(monkeypatch):
    calls = _spy_gather(monkeypatch)
    monkeypatch.setenv("STUDIO_SOCIAL_RESEARCH", "1")
    analyze_customer(_facts(ig_handle="sarahk"), use_llm=False)
    assert len(calls) == 1


def test_analyst_never_fetches_without_a_handle(monkeypatch):
    calls = _spy_gather(monkeypatch)
    analyze_customer(_facts(), use_llm=False, fetch_social=True)
    assert calls == []  # no customer-provided handle -> no lookup, ever


def test_param_social_overrides_fetch(monkeypatch):
    calls = _spy_gather(monkeypatch)
    analyze_customer(
        _facts(ig_handle="sarahk"), social="caller-provided context",
        use_llm=False, fetch_social=True,
    )
    assert calls == []  # explicit social wins; the parameter override is kept


def test_fetch_failure_is_honest_none(monkeypatch):
    def boom(facts, enabled=False):
        raise RuntimeError("egress blocked")

    monkeypatch.setattr("studio.customer_research.gather_social_context", boom)
    prof = analyze_customer(
        _facts(ig_handle="sarahk"), use_llm=False, fetch_social=True
    )
    # Deterministic floor stands; nothing social-derived was fabricated.
    assert prof.umbrella_category.value  # profile still produced
    assert all(
        f.evidence_source != SRC_SOCIAL
        for _, f in prof.scalar_fields()
        if f.signal != INSUFFICIENT
    )
