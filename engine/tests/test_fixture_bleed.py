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

SMOKING GUN (super's live drive, 2026-07-03): three real HELD drafts staged for
skindesign customers via ``research_and_stage_leads`` were signed "it's Rae from
Ladies First" and fabricated an implied history ("work with you again") for leads
whose row carries name+email ONLY. The second half of this file pins the nets that
make that impossible:

* the ``implied_relationship`` personalization rule — "again"/"back"/"been a while"
  phrasing violates unless REAL prior-relationship evidence is on file;
* the foreign-tenant identity guard — a draft for tenant T may never contain
  another tenant's display name or handle;
* ``_build_email_prompt`` reframes a relationship-implying goal as a FIRST contact
  when no relationship evidence exists, and says so as a hard rule;
* the copywriter instructions forbid inventing a sender identity / claims when the
  tenant has none on file;
* ``_research_and_stage_sync`` (the one staging loop that had NO guards) now skips
  a violating draft with a concrete reason — it never reaches the pending queue.
"""

from __future__ import annotations

import pytest

from archetypes.classify import _instructions
from cells.content_brief import Platform
from cells.copywriter import build_copywriter_email_instructions
from cells.identity_guard import foreign_identity_violations
from cells.personalization_guard import facts_view, personalization_violations
from cells.strategy import build_strategy_prompt
from config.loader import describe_tenant
from contentrun import _build_prompt
from research.agent import build_findings_prompt, build_queries_prompt
from studio.customer_research import (
    _build_email_prompt,
    build_outreach_draft,
    resolve_brand_voice,
)
from studio.documents import seed_tenant_documents
from studio.evidence import resolve_brand_voice_doc

# The literal failure observed live: fixture identity + fabricated relationship.
_SMOKING_GUN_SUBJECT = "It's Rae from Ladies First"
_SMOKING_GUN_BODY = (
    "Hi there, it's Rae from Ladies First. We specialize in custom color and floral "
    "work, and the people I have tattooed usually want to work with me again. "
    "I'd love to work with you again too."
)

# Tenants that must NEVER be described as "women-led": the real client (Skin Design
# Tattoo, onboarded pack #113) and the ink-studio fixture (Brooklyn fine-line — a
# DIFFERENT studio). Neither is the ladies8391 women-first fixture.
_NON_WOMENLED = ("skindesign", "ink-studio")

# A tenant with genuinely NO pack on disk — the honest-empty path (a real client not
# yet onboarded). skindesign USED to be this; it now has a real pack (#113), so the
# no-pack assertions must use a tenant that truly has no file.
_NO_PACK_TENANT = "no-such-tenant-xyz"


# --------------------------------------------------------------------------- #
# r9 — describe_tenant: honest pack descriptor, honest-empty when no pack
# --------------------------------------------------------------------------- #
def test_describe_tenant_no_pack_is_bare_handle():
    # A tenant with no pack gets ONLY its handle — no niche, no voice claim, nothing
    # fabricated.
    assert describe_tenant(_NO_PACK_TENANT) == f"@{_NO_PACK_TENANT}"


def test_describe_tenant_real_skindesign_pack_is_honest_and_not_womenled():
    # The onboarded real client resolves its REAL identity — never the fixture's.
    d = describe_tenant("skindesign")
    assert d.startswith("@skindesign")
    assert "Skin Design Tattoo" in d
    assert "women-led" not in d.lower()
    assert "ladies" not in d.lower()


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
    # A tenant with no pack gets honest-empty voice — never the fixture's.
    assert resolve_brand_voice(_NO_PACK_TENANT) == ("", ())


def test_resolve_brand_voice_real_skindesign_is_its_own_voice_not_ladies8391():
    # The onboarded real client resolves ITS OWN voice + claims. (The pack legitimately
    # BANS ladies8391's positioning as an explicit anti-bleed instruction, so we assert
    # POSITIVE skindesign markers rather than mere absence of the fixture's words.)
    voice, claims = resolve_brand_voice("skindesign")
    assert voice.strip() and claims
    low = voice.lower()
    assert "full-day" in low or "full day" in low or "payment plan" in low
    # skindesign's real approved claims — never the fixture's women-first/reclaim ones.
    joined = " ".join(claims).lower()
    assert "skin design" in joined or "full-day" in joined or "klarna" in joined


# --------------------------------------------------------------------------- #
# r8 — resolve_brand_voice_doc never returns another tenant's doc
# --------------------------------------------------------------------------- #
def test_resolve_brand_voice_doc_no_pack_is_none():
    assert resolve_brand_voice_doc(_NO_PACK_TENANT) is None


def test_resolve_brand_voice_doc_real_skindesign_is_its_own_doc():
    # The onboarded client resolves its OWN structured doc — never another tenant's.
    doc = resolve_brand_voice_doc("skindesign")
    assert doc is not None and doc.tenant_id == "skindesign"


def test_resolve_brand_voice_doc_none_without_default_is_none(monkeypatch):
    _clear_tenant_env(monkeypatch)
    assert resolve_brand_voice_doc(None) is None


# --------------------------------------------------------------------------- #
# r8 — the Ladies First playbook can never seed a real client's RAG
# --------------------------------------------------------------------------- #
def test_seed_tenant_documents_refuses_non_fixture_tenant():
    # Returns None BEFORE touching any DB — no seed for the real client.
    assert seed_tenant_documents("skindesign") is None


def test_post_campaign_label_records_real_tenant_not_ladies8391():
    """A social post caption must ground brand_voice=<its tenant>, never the hardcoded
    fixture id (adversarial finding: the label hardcoded 'ladies8391' for every tenant)."""
    from studio.post_campaign import VoiceBundle, compose_caption

    vb = VoiceBundle(resolved=True, prefer=["full day"], tenant_id="skindesign")
    cap = compose_caption(platform="instagram", artist="Angel", pick=None, voice=vb)
    assert "brand_voice=skindesign" in cap.grounding
    assert not any("ladies8391" in g for g in cap.grounding), cap.grounding


def test_resolve_voice_none_is_honest_empty_without_flag(monkeypatch):
    """resolve_voice(None) must not borrow the ladies8391 fixture voice — same dev-flag
    gate as resolve_brand_voice (r8)."""
    from studio.post_campaign import resolve_voice

    _clear_tenant_env(monkeypatch)
    assert resolve_voice(None).resolved is False
    assert resolve_voice("skindesign").tenant_id == "skindesign"


def test_seed_allowlist_covers_the_studio_default_demo_tenant():
    """The studio host's default STUDIO_TENANT_ID is 'demo' (agui) — a sandbox, not a
    real client. It must stay in the seed allowlist or the demo loses its first-run
    doc. (Membership check only: actually seeding needs a DB.)"""
    from studio.documents import _FIXTURE_SEED_TENANTS

    assert {"ladies8391", "ink-studio", "demo"} <= _FIXTURE_SEED_TENANTS
    assert "skindesign" not in _FIXTURE_SEED_TENANTS


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


# =========================================================================== #
# SMOKING GUN — the live-observed fabrication and the nets that now refuse it
# =========================================================================== #

_FACTLESS = {"name": "Dana", "email": "dana@example.com"}


# --------------------------------------------------------------------------- #
# implied_relationship rule: relationship phrasing needs REAL relationship evidence
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("phrase", [
    "I'd love to work with you again.",
    "We'd love to have you back.",
    "Hope to see you again soon.",
    "Welcome back!",
    "It's been a while since we caught up.",
    "We've been thinking about you.",
    "Time for your next tattoo?",
    "The people I have tattooed keep coming back.",
    "As a returning client you know the drill.",
])
def test_implied_relationship_violates_for_factless_lead(phrase):
    viols = personalization_violations(phrase, facts_view(_FACTLESS))
    assert any("implied_relationship" in v for v in viols), (phrase, viols)


@pytest.mark.parametrize("evidence", [
    {"tattoo_history": [{"style": "floral"}]},
    {"persona_traits": {"lifecycle_stage": "lapsing"}},
    {"persona_traits": {"win_back_candidate": True}},
])
def test_implied_relationship_grounded_by_real_evidence(evidence):
    facts = facts_view({**_FACTLESS, **evidence})
    assert personalization_violations("It's been a while — we'd love to see you again.", facts) == []


class _Profile:
    """Minimal stand-in for a PsychProfile carrying only the had_conversation flag."""
    def __init__(self, had_conversation: bool):
        self.had_conversation = had_conversation


def test_implied_relationship_grounded_only_by_a_REAL_conversation():
    # A profile with a REAL conversation grounds "reconnect" phrasing...
    facts = facts_view(_FACTLESS, profile=_Profile(had_conversation=True))
    assert personalization_violations("Good to hear from you again!", facts) == []


def test_floor_profile_without_conversation_does_not_ground(monkeypatch):
    # ...but a deterministic FLOOR profile (had_conversation=False — always produced by
    # analyze_customer) must NOT ground it. This was the adversarial no-op: any profile
    # set prior_conversation=True, disabling the guard on the whole provided-leads path.
    facts = facts_view(_FACTLESS, profile=_Profile(had_conversation=False))
    viols = personalization_violations("Good to work with you again!", facts)
    assert any("implied_relationship" in v for v in viols), viols


def test_lead_no_visit_lifecycle_does_not_ground_welcome_back():
    # 'lead-no-visit' = NEVER visited; it is in the research cohort's default set but must
    # NOT ground "welcome back". (Adversarial finding: presence of ANY lifecycle grounded.)
    facts = facts_view({**_FACTLESS, "persona_traits": {"lifecycle_stage": "lead-no-visit"}})
    viols = personalization_violations("Welcome back — we'd love to have you back!", facts)
    assert any("implied_relationship" in v for v in viols), viols


@pytest.mark.parametrize("phrase", [
    # Broadened win-back phrasings (adversarial gap list).
    "We haven't seen you in a while.",
    "It's been too long!",
    "As a past client, you already know our work.",
    "Everyone I have tattooed keeps coming back.",
    "Let's work with you once more.",
    "Let's pick up where we left off.",
    "Great meeting you last month.",
    "Ready for round two?",
    # Prior-CONVERSATION claim family (was entirely unmatched).
    "Per our last conversation, here's the plan.",
    "As we discussed, I held the slot.",
    "Following up on our chat.",
    "Like we talked about, deposits are refundable.",
    "So great to reconnect!",
    "When we spoke earlier you mentioned a sleeve.",
])
def test_broadened_relationship_and_conversation_phrasings_are_caught(phrase):
    viols = personalization_violations(phrase, facts_view(_FACTLESS))
    assert any("implied_relationship" in v for v in viols), (phrase, viols)


@pytest.mark.parametrize("clean", [
    # The opt-out line every deterministic draft carries — must NEVER violate.
    "If you'd rather not hear from me, just reply STOP and I won't reach out again.",
    # Honest first-contact copy.
    "Hi Dana, wanted to reach out and say hello.",
    "Hi Dana, hope Las Vegas is treating you well — wanted to say hello.",
    # 'come back' about ink healing, not the person returning (guard wants in|and|to).
    "Packed color that heals bright.",
    # Genuine first-contact invitations must stay clean.
    "Reply YES and I'll send over a booking link.",
    "We'd love to help whenever you're ready.",
    "If you'd like to chat about a design, just reply.",
])
def test_honest_first_contact_copy_never_trips_the_relationship_rule(clean):
    assert personalization_violations(clean, facts_view(_FACTLESS)) == []


# --------------------------------------------------------------------------- #
# foreign-tenant identity guard — variant hardening (adversarial finding)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("copy", [
    "it's Rae from Ladies First",           # spaced display_name (the live signature)
    "check out LadiesFirst for flash",      # run-together, no space
    "follow @ladiesfirst on insta",         # handle form
    "warmly,\nLadies\nFirst",               # newline-split name
    "sent on behalf of ladies8391",         # the tenant id itself
])
def test_foreign_identity_variants_all_caught_on_skindesign(copy):
    viols = foreign_identity_violations(copy, "skindesign")
    assert viols and "ladies8391" in viols[0], (copy, viols)


@pytest.mark.parametrize("copy", [
    "we love bold blackwork and fine-line ink",   # 'ink' must not trip ink-studio
    "thinking about your next piece?",            # 'thinking' must not trip anything
    "Skin Design Tattoo has six locations",       # own identity is allowed
    "our studio does great custom work",          # generic 'studio' word
])
def test_identity_guard_precision_on_ordinary_skindesign_copy(copy):
    assert foreign_identity_violations(copy, "skindesign") == [], copy


# --------------------------------------------------------------------------- #
# foreign-tenant identity guard
# --------------------------------------------------------------------------- #
def test_foreign_identity_catches_ladies_first_on_skindesign():
    viols = foreign_identity_violations(_SMOKING_GUN_BODY, "skindesign")
    assert viols and "ladies8391" in viols[0], viols


def test_foreign_identity_catches_the_handle_too():
    viols = foreign_identity_violations("Follow us at ladies8391 for flash!", "skindesign")
    assert viols and "ladies8391" in viols[0], viols


def test_own_identity_is_allowed():
    # ladies8391 naming ITSELF is legitimate — only FOREIGN identities violate.
    assert foreign_identity_violations("Hi, it's Rae from Ladies First.", "ladies8391") == []


def test_foreign_identity_clean_draft_passes():
    assert foreign_identity_violations(
        "Hi Dana, wanted to reach out and say hello.", "skindesign") == []


# --------------------------------------------------------------------------- #
# _build_email_prompt: relationship-implying goal -> honest first contact
# --------------------------------------------------------------------------- #
_GENERIC_ANGLE = {
    "key": "generic", "label": "an honest general introduction",
    "basis": "no distinguishing research or history on file",
    "inferred": False, "generic": True,
}


def test_email_prompt_reframes_winback_goal_for_factless_lead():
    prompt = _build_email_prompt(
        dict(_FACTLESS), goal="win back lapsed clients", research=[],
        angle=_GENERIC_ANGLE,
    )
    assert "NO PRIOR RELATIONSHIP" in prompt
    assert "first contact" in prompt.lower()
    # The relationship-implying internal goal is NOT handed to the model verbatim.
    assert "win back lapsed clients" not in prompt


def test_email_prompt_keeps_winback_goal_with_real_history():
    facts = {**_FACTLESS, "tattoo_history": [{"style": "floral"}]}
    prompt = _build_email_prompt(
        facts, goal="win back lapsed clients", research=[], angle=_GENERIC_ANGLE,
    )
    assert "win back lapsed clients" in prompt
    assert "NO PRIOR RELATIONSHIP" not in prompt


def test_email_prompt_keeps_nonrelationship_goal_for_factless_lead():
    prompt = _build_email_prompt(
        dict(_FACTLESS), goal="promote the spring flash event", research=[],
        angle=_GENERIC_ANGLE,
    )
    assert "promote the spring flash event" in prompt
    # Still stamped as a first contact (no relationship evidence).
    assert "NO PRIOR RELATIONSHIP" in prompt


# --------------------------------------------------------------------------- #
# copywriter instructions: no identity / no claims on file -> hard constraints
# --------------------------------------------------------------------------- #
def test_email_instructions_forbid_invented_identity_when_voice_empty():
    instr = build_copywriter_email_instructions("", ())
    assert "SENDER IDENTITY: NONE ON FILE" in instr
    assert "Approved claims: NONE on file" in instr


def test_email_instructions_with_real_voice_have_no_none_blocks():
    instr = build_copywriter_email_instructions("## Tone:\n- warm", ("We do color",))
    assert "SENDER IDENTITY: NONE ON FILE" not in instr
    assert "Approved claims: NONE on file" not in instr
    assert "BRAND VOICE" in instr


# --------------------------------------------------------------------------- #
# _research_and_stage_sync (the smoking-gun path): guards wired, skip-with-reason
# --------------------------------------------------------------------------- #
class _FakeMemoryStore:
    def __init__(self, *a, **kw): ...
    def ensure_schema(self): ...
    def write(self, **kw): ...


def _stage_run(monkeypatch, leads, *, tenant="skindesign", draft_fn=None):
    """Drive the REAL _research_and_stage_sync with fakes for DB/memory so the guard
    wiring is exercised hermetically (no Postgres needed)."""
    import actions.store as astore
    import memory as memmod
    import studio.customer_research as cr
    from studio import agui

    staged_rows: list[dict] = []
    monkeypatch.setattr(astore, "ensure_schema", lambda dsn=None: None)

    def _rec(**kw):
        staged_rows.append(kw)
        return f"act_{len(staged_rows)}"

    monkeypatch.setattr(astore, "record_pending_action", _rec)
    monkeypatch.setattr(memmod, "MemoryStore", _FakeMemoryStore)
    monkeypatch.setattr(
        cr, "lookup_leads", lambda tid, specs, dsn=None, memory_store=None: leads
    )
    if draft_fn is not None:
        monkeypatch.setattr(cr, "build_outreach_draft", draft_fn)

    plan = agui.CampaignPlan(goal="win back lapsed clients")
    summary = agui._research_and_stage_sync(
        plan, "sess-gun", tenant, None,
        emails=[lead["email"] for lead in leads], limit=10,
    )
    return summary, staged_rows


def test_smoking_gun_draft_is_refused_not_staged(monkeypatch):
    """The EXACT live failure: a draft carrying the fixture identity + fabricated
    relationship for a skindesign customer is SKIPPED with a concrete reason —
    it never reaches the pending queue."""
    _clear_tenant_env(monkeypatch)

    def _gun_draft(facts, **kw):
        return {
            "channel": "gmail", "target": facts["email"],
            "subject": _SMOKING_GUN_SUBJECT, "draft": _SMOKING_GUN_BODY,
            "grounding": ["name=Dana"], "customer_id": facts["customer_id"],
        }

    leads = [{"customer_id": "sd1", "name": "Dana", "email": "dana@example.com",
              "email_opt_in": True}]
    summary, staged_rows = _stage_run(monkeypatch, leads, draft_fn=_gun_draft)

    assert summary["n_drafts"] == 0
    assert staged_rows == []
    assert len(summary["skipped"]) == 1
    assert "ladies8391" in summary["skipped"][0]["reason"]


def test_skindesign_three_customer_run_stages_clean_drafts(monkeypatch):
    """The Accept line: staging the same 3-customer run for skindesign produces
    drafts with ZERO Ladies First identity/claims and ZERO implied-history phrasing
    (deterministic path — no Anthropic key)."""
    _clear_tenant_env(monkeypatch)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("SCALERS_OUTREACH_LLM", raising=False)

    leads = [
        {"customer_id": f"sd{i}", "name": n, "email": f"{n.lower()}@example.com",
         "email_opt_in": True}
        for i, n in enumerate(["Dana", "Priya", "Mel"], 1)
    ]
    summary, staged_rows = _stage_run(monkeypatch, leads)

    assert summary["n_drafts"] == 3, summary
    for row in staged_rows:
        copy = f"{row.get('subject') or ''}\n{row['draft']}"
        assert foreign_identity_violations(copy, "skindesign") == [], copy
        assert personalization_violations(copy, facts_view({})) == [], copy
        low = copy.lower()
        assert "ladies first" not in low and "rae" not in low, copy


def test_fixture_winback_cohort_still_stages(monkeypatch):
    """Fixture tenants keep working: a ladies8391 lead with a REAL win-back persona
    signal stages its re-engagement draft (grounded 'been a while' phrasing passes)."""
    _clear_tenant_env(monkeypatch)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("SCALERS_OUTREACH_LLM", raising=False)

    leads = [{
        "customer_id": "lf1", "name": "Jess", "email": "jess@example.com",
        "email_opt_in": True,
        "persona_traits": {"win_back_candidate": True, "lifecycle_stage": "lapsing"},
    }]
    summary, staged_rows = _stage_run(monkeypatch, leads, tenant="ladies8391")

    assert summary["n_drafts"] == 1, summary
    assert summary["skipped"] == []
    assert len(staged_rows) == 1
