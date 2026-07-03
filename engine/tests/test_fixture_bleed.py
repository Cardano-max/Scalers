"""Regression: kill the ladies8391 fixture-identity bleed + the hardcoded
"women-led tattoo studio" fabrication (CustomerAcq-wwy.7, ranks r8 + r9).

Before this bead every prompt-builder hardcoded ``"a women-led tattoo studio"`` and
every unspecified-tenant path silently borrowed the ``ladies8391`` FIXTURE voice /
playbook / brand-voice doc. For a real client (e.g. ``skindesign``) that is a
fabricated identity + a cross-tenant leak.

These tests pin the honest behavior:

* :func:`config.loader.describe_tenant` renders the tenant's REAL pack descriptor,
  degrading to the bare handle when no pack resolves — never a fabricated niche.
* Every prompt-builder takes that descriptor as a REQUIRED param; rendered prompts
  for ``skindesign`` / ``ink-studio`` NEVER contain "women-led".
* ``resolve_brand_voice(None)`` is honest-empty (no fixture stand-in) unless a real
  tenant is configured or the explicit dev flag is set.
* ``resolve_brand_voice_doc`` never returns another tenant's doc.
* ``seed_tenant_documents('skindesign')`` returns None (the Ladies First playbook
  can never seed a real client's RAG).
"""

from __future__ import annotations

import pytest

from archetypes.classify import _instructions
from cells.content_brief import Platform
from cells.strategy import build_strategy_prompt
from config.loader import describe_tenant
from contentrun import _build_prompt
from research.agent import build_findings_prompt, build_queries_prompt
from studio.customer_research import build_outreach_draft, resolve_brand_voice
from studio.documents import seed_tenant_documents
from studio.evidence import resolve_brand_voice_doc

# Tenants that must NEVER be described as "women-led": the real client (no pack yet)
# and the ink-studio fixture (Brooklyn fine-line, a DIFFERENT studio).
_NON_WOMENLED = ("skindesign", "ink-studio")


# --------------------------------------------------------------------------- #
# r9 — describe_tenant: honest pack descriptor, honest-empty when no pack
# --------------------------------------------------------------------------- #
def test_describe_tenant_no_pack_is_bare_handle():
    # A real client not yet onboarded (no pack) gets ONLY its handle — no niche,
    # no voice claim, nothing fabricated.
    assert describe_tenant("skindesign") == "@skindesign"


def test_describe_tenant_uses_real_pack_display_name_and_positioning():
    d = describe_tenant("ink-studio")
    assert d.startswith("@ink-studio")
    assert "Ink & Iron Tattoo Studio" in d          # the pack's real display_name
    assert "Brooklyn fine-line" in d                # the pack's real positioning
    assert "women-led" not in d.lower()


def test_describe_tenant_never_fabricates_womenled_for_real_or_inkstudio():
    for tid in _NON_WOMENLED:
        assert "women-led" not in describe_tenant(tid).lower()


# --------------------------------------------------------------------------- #
# r9 — every prompt-builder takes the descriptor and never hardcodes the niche
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("tid", _NON_WOMENLED)
def test_strategy_prompt_never_hardcodes_womenled(tid):
    prompt = build_strategy_prompt(describe_tenant(tid), "Spring booking push")
    assert "women-led" not in prompt.lower()
    assert describe_tenant(tid) in prompt


@pytest.mark.parametrize("tid", _NON_WOMENLED)
def test_content_prompt_never_hardcodes_womenled(tid):
    prompt = _build_prompt(describe_tenant(tid), "Spring booking push", Platform.INSTAGRAM)
    assert "women-led" not in prompt.lower()
    assert describe_tenant(tid) in prompt


@pytest.mark.parametrize("tid", _NON_WOMENLED)
def test_research_prompts_never_hardcode_womenled(tid):
    q = build_queries_prompt(describe_tenant(tid), "Spring booking push")
    f = build_findings_prompt(
        describe_tenant(tid), "Spring booking push",
        [{"title": "t", "url": "https://x.example", "snippet": "s"}],
    )
    assert "women-led" not in q.lower()
    assert "women-led" not in f.lower()


@pytest.mark.parametrize("tid", _NON_WOMENLED)
def test_classify_instructions_never_hardcode_womenled(tid):
    instr = _instructions(describe_tenant(tid))
    assert "women-led" not in instr.lower()
    assert describe_tenant(tid) in instr


# --------------------------------------------------------------------------- #
# r8 — resolve_brand_voice(None) is honest-empty (no ladies8391 stand-in)
# --------------------------------------------------------------------------- #
def _clear_tenant_env(monkeypatch):
    monkeypatch.delenv("SCALERS_TENANT_ID", raising=False)
    monkeypatch.delenv("SCALERS_ALLOW_FIXTURE_TENANT", raising=False)


def test_resolve_brand_voice_none_is_empty_without_flag(monkeypatch):
    _clear_tenant_env(monkeypatch)
    assert resolve_brand_voice(None) == ("", ())


def test_resolve_brand_voice_none_honors_fixture_dev_flag(monkeypatch):
    _clear_tenant_env(monkeypatch)
    monkeypatch.setenv("SCALERS_ALLOW_FIXTURE_TENANT", "1")
    # With the explicit dev flag, the demo tenant still works: None resolves to the
    # SAME thing as asking for ladies8391 explicitly.
    assert resolve_brand_voice(None) == resolve_brand_voice("ladies8391")


def test_resolve_brand_voice_none_honors_configured_tenant(monkeypatch):
    _clear_tenant_env(monkeypatch)
    monkeypatch.setenv("SCALERS_TENANT_ID", "ink-studio")
    assert resolve_brand_voice(None) == resolve_brand_voice("ink-studio")


def test_resolve_brand_voice_unknown_tenant_is_empty():
    # A real client with no pack gets honest-empty voice — never the fixture's.
    assert resolve_brand_voice("skindesign") == ("", ())


# --------------------------------------------------------------------------- #
# r8 — resolve_brand_voice_doc never returns another tenant's doc
# --------------------------------------------------------------------------- #
def test_resolve_brand_voice_doc_no_pack_is_none():
    assert resolve_brand_voice_doc("skindesign") is None


def test_resolve_brand_voice_doc_none_without_default_is_none(monkeypatch):
    _clear_tenant_env(monkeypatch)
    assert resolve_brand_voice_doc(None) is None


# --------------------------------------------------------------------------- #
# r8 — the Ladies First playbook can never seed a real client's RAG
# --------------------------------------------------------------------------- #
def test_seed_tenant_documents_refuses_non_fixture_tenant():
    # Returns None BEFORE touching any DB — no seed for the real client.
    assert seed_tenant_documents("skindesign") is None


# --------------------------------------------------------------------------- #
# r8 — a provided-leads draft for a real client never records the fixture voice
# --------------------------------------------------------------------------- #
def test_outreach_draft_grounding_never_records_ladies8391(monkeypatch):
    """A skindesign outreach draft records ``brand_voice=skindesign`` or NO brand_voice
    row — NEVER ``brand_voice=ladies8391``. With no Anthropic key the copy takes the
    deterministic path; either way the fixture voice never leaks into the grounding."""
    _clear_tenant_env(monkeypatch)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("SCALERS_OUTREACH_LLM", raising=False)

    facts = {
        "customer_id": "c1", "name": "Jamie", "city": "Las Vegas",
        "email": "jamie@example.com", "email_opt_in": True,
    }
    draft = build_outreach_draft(facts, goal="win back", tenant_id="skindesign", channel="gmail")
    grounding = draft["grounding"]

    assert not any("ladies8391" in g for g in grounding), grounding
    brand_rows = [g for g in grounding if g.startswith("brand_voice=")]
    # skindesign has no pack -> honest-empty voice -> no brand_voice row at all.
    assert brand_rows == [] or brand_rows == ["brand_voice=skindesign"], grounding
