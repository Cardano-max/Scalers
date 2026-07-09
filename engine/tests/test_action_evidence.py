"""Per-draft evidence assembler (studio.evidence) — REAL-ONLY guarantees.

These exercise the PURE assembler (no Postgres): given the rows a real run
persisted, the evidence surfaces ONLY what THAT draft genuinely used. The core
guarantee under test: a draft whose grounding cites no research source shows NO
research source, and brand voice is shown only when the draft actually wrote in it.
"""

from __future__ import annotations

from studio.evidence import (
    BrandVoiceDoc,
    assemble_action_evidence,
    resolve_brand_voice_doc,
)


def _brand_doc() -> BrandVoiceDoc:
    return BrandVoiceDoc(
        tenant_id="ladies8391",
        tone=["warm, direct"],
        structure=["one idea per line"],
        prefer=["made for you", "reclaim"],
        ban=["slay", "boss babe"],
        approved_claims=["Woman-owned, appointment-only studio in Austin, TX."],
        source="skills/brand-voice/tenants/ladies8391/brand-dna.md",
    )


def _action(**over):
    base = {
        "id": "act_full",
        "run_id": "team-camp_abc-deadbeef0001",
        "tenant_id": "ladies8391",
        "channel": "gmail",
        "target": "rae@example.com",
        "status": "pending",
        "conf": None,
        "threshold": None,
        "esc_label": "Provided-lead outreach — operator approval required",
        "idempotency_key": "team-camp_abc-deadbeef0001:cust_777",
        "decision_id": None,
    }
    base.update(over)
    return base


def _agent_runs_full():
    """A real per-lead run: researcher (2 cited sources) + draft (used brand voice +
    cited one of those urls) + a jury summary."""
    return [
        {
            "id": "ar_research", "role": "researcher", "model": "firecrawl+customer_db",
            "input": {"customer_id": "cust_777", "name": "Rae"},
            "output": {
                "cited": 2,
                "sources": [
                    {"url": "https://studio-a.test/about", "title": "About A", "snippet": "neo-trad", "query": "rae austin tattoo"},
                    {"url": "https://studio-a.test/gallery", "title": "Gallery A", "snippet": "florals", "query": "rae austin tattoo"},
                ],
                "lead": "Rae",
                "customer_id": "cust_777",
                "db_history": {"city": "Austin", "lifecycle": "lapsing", "win_back_candidate": True},
                "degraded": False,
            },
        },
        {
            "id": "ar_draft", "role": "draft", "model": "anthropic:claude-sonnet-4-6",
            "input": {"customer_id": "cust_777", "channel": "gmail"},
            "output": {
                "hook": "Hello from one studio to another", "caption": "Hi Rae ...",
                "channel": "gmail",
                "grounding": [
                    "name=Rae", "city=Austin", "interest/aesthetic=florals",
                    "brand_voice=ladies8391",
                    "research:https://studio-a.test/about",
                    "copy=copywriter_email_cell",
                ],
            },
        },
        {
            "id": "ar_jury", "role": "jury", "model": "anthropic:claude-opus-4-8",
            "input": {"n_leads": 1, "lead_source": "provided"},
            "output": {"aggregate": 1.0, "decision": "review", "note": "1 draft staged HELD"},
        },
    ]


def test_full_draft_surfaces_only_what_it_used():
    ev = assemble_action_evidence(
        action=_action(),
        agent_runs=_agent_runs_full(),
        research_sources=[],
        memories=[{"text": "Staged gmail outreach to Rae", "metadata": {"kind": "outreach"}}],
        brand_voice=_brand_doc(),
        campaign_id="camp_abc",
    )
    # Brand voice IS shown (the draft wrote in it) with the real dimensions.
    assert ev.brand_voice is not None
    assert ev.brand_voice.used is True
    assert "slay" in ev.brand_voice.ban
    # Exactly the ONE research url the draft cited — NOT both researcher sources.
    assert [s.url for s in ev.research_sources] == ["https://studio-a.test/about"]
    # And it is enriched from the researcher step (real title/snippet/query).
    assert ev.research_sources[0].title == "About A"
    assert ev.research_sources[0].query == "rae austin tattoo"
    # Customer facts come from the grounding audit + db history.
    assert ev.customer is not None
    assert ev.customer.name == "Rae"
    assert ev.customer.city == "Austin"
    assert ev.customer.win_back_candidate is True
    # Tool calls reflect the real cells invoked.
    names = {t.name for t in ev.tool_calls}
    assert "copywriter_email_cell" in names
    assert "firecrawl_search" in names
    # Producing agent + jury surfaced.
    assert ev.created_by is not None and ev.created_by.role == "draft"
    assert ev.jury is not None and ev.jury.decision == "review"
    assert ev.lead_memories and ev.lead_memories[0].text.startswith("Staged gmail")
    assert ev.is_real_only is True


def test_draft_with_no_research_shows_none():
    """REAL-ONLY: a draft whose grounding cites no research:url surfaces ZERO research
    sources, even when the run's researcher step found some."""
    runs = _agent_runs_full()
    # Strip the research:url from THIS draft's grounding (it used none).
    runs[1]["output"]["grounding"] = ["name=Rae", "city=Austin", "copy=deterministic_template"]
    ev = assemble_action_evidence(
        action=_action(),
        agent_runs=runs,
        research_sources=[],
        brand_voice=_brand_doc(),
    )
    assert ev.research_sources == []
    # And brand voice is NOT shown (no brand_voice= marker; deterministic copy).
    assert ev.brand_voice is None
    # The tool call reflects the deterministic path, not the copywriter cell.
    assert any(t.name == "deterministic_template" for t in ev.tool_calls)


def test_brand_documents_surface_only_passages_the_draft_used():
    """The copywriter node records the playbook passages it retrieved on the draft
    step's ``documents_used`` input; evidence surfaces them as Brand documents chips —
    real-only, so a draft that used none shows none."""
    action = _action(id="act_post", channel="instagram",
                     idempotency_key="team-camp_abc-deadbeef0001:as_xyz")
    used = [
        {"document": "Ladies First Brand & Campaign Playbook",
         "heading": "Brand identity & voice", "document_id": "doc_seed"},
    ]
    runs = [{
        "id": "ar_draft", "role": "draft", "model": "anthropic:claude-sonnet-4-6",
        "input": {"channel": "instagram", "tenant_id": "ladies8391",
                  "brand_voice_applied": True, "documents_used": used},
        "output": {"caption": "warm florals", "headline": "Reclaim your story"},
    }]
    ev = assemble_action_evidence(action=action, agent_runs=runs)
    assert [d.document for d in ev.brand_documents] == ["Ladies First Brand & Campaign Playbook"]
    assert ev.brand_documents[0].heading == "Brand identity & voice"
    # a draft with no documents_used shows no document chip
    runs[0]["input"]["documents_used"] = []
    assert assemble_action_evidence(action=action, agent_runs=runs).brand_documents == []


def test_brand_voice_hidden_when_not_used_even_if_doc_available():
    runs = _agent_runs_full()
    runs[1]["output"]["grounding"] = ["name=Rae"]  # no brand_voice marker
    ev = assemble_action_evidence(action=_action(), agent_runs=runs, brand_voice=_brand_doc())
    assert ev.brand_voice is None


def test_content_path_brand_voice_via_applied_flag():
    """The content/social path has no grounding list; it proves voice use with a
    persisted ``brand_voice_applied`` input flag instead."""
    action = _action(id="act_post", channel="instagram",
                     idempotency_key="team-camp_abc-deadbeef0001:as_xyz")
    runs = [
        {
            "id": "ar_draft", "role": "draft", "model": "anthropic:claude-sonnet-4-6",
            "input": {"channel": "instagram", "tenant_id": "ladies8391", "brand_voice_applied": True},
            "output": {"caption": "bold florals", "headline": "Reclaim your story"},
        },
        {
            "id": "ar_critic", "role": "critic", "model": "anthropic:claude-opus-4-8",
            "input": {"asset_id": "as_xyz"},
            "output": {"verdict": "approve", "rationale": "on voice, no unapproved claims", "confidence": 0.9},
        },
    ]
    # The run's real cited sources (research_sources table) back the content path.
    rs = [{"url": "https://trends.test/2026", "title": "Trends", "snippet": "color", "query": "neo-trad trends"}]
    ev = assemble_action_evidence(action=action, agent_runs=runs, research_sources=rs,
                                  brand_voice=_brand_doc())
    assert ev.brand_voice is not None and ev.brand_voice.used is True
    assert [s.url for s in ev.research_sources] == ["https://trends.test/2026"]
    assert ev.critic_review is not None and ev.critic_review.verdict == "approve"


def test_no_run_id_degrades_to_ids_only_not_fabrication():
    ev = assemble_action_evidence(
        action=_action(run_id=None, idempotency_key="loose-key"),
        agent_runs=[],
        brand_voice=_brand_doc(),
    )
    assert ev.research_sources == []
    assert ev.brand_voice is None
    assert ev.tool_calls == []
    assert ev.created_by is None


def test_camel_case_on_the_wire():
    ev = assemble_action_evidence(action=_action(), agent_runs=_agent_runs_full(),
                                  brand_voice=_brand_doc(), campaign_id="camp_abc")
    dumped = ev.model_dump(by_alias=True)
    assert "researchSources" in dumped
    assert "brandVoice" in dumped
    assert "isRealOnly" in dumped
    assert dumped["brandVoice"]["approvedClaims"]


def test_personalization_angle_and_source_type_surface():
    """The per-lead distinct angle + honest rationale (recorded on the draft step) and
    the per-source TYPE (recorded on the researcher step) surface on the evidence so the
    operator can SEE why this draft differs from the others and that sources are diverse."""
    runs = _agent_runs_full()
    # The draft step carries the structured personalization fields.
    runs[1]["output"].update({
        "angle": "their past florals work with us",
        "angle_key": "past-work",
        "why_different": "Personalized on their past florals work with us; grounded on past florals piece on file.",
        "generic": False,
        "inferred": False,
    })
    # The researcher step tags each source with its derived type.
    runs[0]["output"]["sources"][0]["source_type"] = "website"
    runs[0]["output"]["sources"][1]["source_type"] = "social"
    ev = assemble_action_evidence(action=_action(), agent_runs=runs, brand_voice=_brand_doc())
    assert ev.personalization is not None
    assert ev.personalization.angle == "their past florals work with us"
    assert ev.personalization.generic is False
    assert "Personalized on" in (ev.personalization.why_different or "")
    # The single cited source (about) is tagged website (real-only: only the cited url).
    assert ev.research_sources and ev.research_sources[0].source_type == "website"
    # camelCase on the wire for the new fields.
    dumped = ev.model_dump(by_alias=True)
    assert "personalization" in dumped and dumped["personalization"]["whyDifferent"]
    assert dumped["researchSources"][0]["sourceType"] == "website"


def test_generic_draft_is_honestly_flagged_not_faked():
    """A thin-data draft records ``generic=True``; evidence surfaces the honest-generic
    label (never dressed up as personalized)."""
    runs = _agent_runs_full()
    runs[1]["output"].update({
        "angle": "an honest general introduction",
        "angle_key": "generic",
        "why_different": "Honest-generic: no distinguishing research or history on file.",
        "generic": True,
        "inferred": False,
    })
    ev = assemble_action_evidence(action=_action(), agent_runs=runs, brand_voice=_brand_doc())
    assert ev.personalization is not None
    assert ev.personalization.generic is True
    assert "Honest-generic" in (ev.personalization.why_different or "")


def test_resolve_brand_voice_doc_loads_real_ladies8391():
    """The real ladies8391 pack resolves to structured dimensions (not faked)."""
    doc = resolve_brand_voice_doc("ladies8391")
    assert doc is not None
    assert doc.tenant_id == "ladies8391"
    assert any("woman" in c.lower() for c in doc.approved_claims)
    assert "slay" in doc.ban
    assert doc.source.endswith("brand-dna.md")
