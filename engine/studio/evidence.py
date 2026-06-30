"""Per-draft EVIDENCE / PROVENANCE — the REAL, real-only audit of what one staged
draft actually used.

Given a staged ``actions`` row (a HELD draft), this assembles the concrete sources,
tools, and agents that produced it — Brand Voice, the Customer/CSV facts, Lead
Memory, Internal Notes, Research Source URLs, Tool Calls, the producing agent +
reasoning, the Critic verdict, and the Jury score — for the console to render as
clean clickable chips (NEVER raw JSON).

REAL-ONLY is the contract, enforced by the JOINS, not by trust:

  * Everything is keyed to the draft's own ``run_id`` (and, for the per-lead
    outreach path, its own ``customer_id`` parsed from ``idempotency_key`` =
    ``"{run_id}:{cust_id}"``). A draft only ever shows evidence from ITS run / ITS
    lead.
  * The draft's ``agent_runs`` ``draft`` step carries the persisted ``grounding``
    audit list (``name=`` / ``city=`` / ``research:<url>`` / ``brand_voice=`` /
    ``copy=``) — the exact set of facts/sources the copy was *allowed* to use. We
    surface only what is in that list. A draft whose grounding cites no research
    source shows NO research source. Brand voice is shown only when the draft
    actually wrote in it (a ``brand_voice=`` marker, or the content path's
    ``brand_voice_applied`` input flag).
  * Memories are the real ``memories`` rows for that lead; research titles/snippets
    are enriched only from the run's real ``researcher`` step / ``research_sources``
    rows. Nothing is fabricated; an absent category is omitted (honest-empty).

:func:`assemble_action_evidence` is a PURE function over already-fetched rows (no
DB) so the real-only logic is unit-tested without Postgres;
:func:`build_action_evidence` is the thin DB wrapper that fetches the rows.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


# --------------------------------------------------------------------------- #
# Typed, camelCase-on-the-wire evidence shapes (the console consumes these).
# --------------------------------------------------------------------------- #
class _Camel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class EvidenceAgent(_Camel):
    role: str | None = None
    model: str | None = None
    reasoning_summary: str | None = None


class EvidenceBrandVoice(_Camel):
    tenant_id: str
    used: bool
    tone: list[str] = []
    structure: list[str] = []
    prefer: list[str] = []
    ban: list[str] = []
    approved_claims: list[str] = []
    source: str = ""


class EvidenceCustomer(_Camel):
    customer_id: str | None = None
    name: str | None = None
    city: str | None = None
    note: str | None = None
    interest: str | None = None
    lifecycle: str | None = None
    last_tattoo_style: str | None = None
    win_back_candidate: bool = False
    facts_used: list[str] = []


class EvidenceMemory(_Camel):
    text: str
    kind: str | None = None
    created_at: str | None = None


class EvidenceResearchSource(_Camel):
    url: str
    title: str | None = None
    snippet: str | None = None
    query: str | None = None


class EvidenceToolCall(_Camel):
    name: str
    detail: str | None = None


class EvidenceCritic(_Camel):
    verdict: str | None = None
    rationale: str | None = None
    model: str | None = None


class EvidenceJury(_Camel):
    aggregate: float | None = None
    decision: str | None = None
    note: str | None = None


class ActionEvidence(_Camel):
    action_id: str
    run_id: str | None = None
    campaign_id: str | None = None
    tenant_id: str
    channel: str | None = None
    target: str | None = None
    status: str | None = None
    created_by: EvidenceAgent | None = None
    brand_voice: EvidenceBrandVoice | None = None
    customer: EvidenceCustomer | None = None
    lead_memories: list[EvidenceMemory] = []
    internal_notes: str | None = None
    research_sources: list[EvidenceResearchSource] = []
    tool_calls: list[EvidenceToolCall] = []
    critic_review: EvidenceCritic | None = None
    jury: EvidenceJury | None = None
    confidence: float | None = None
    threshold: float | None = None
    confidence_reason: str | None = None
    reasoning_url: str | None = None
    is_real_only: bool = True


# A resolved brand-voice document (structured dimensions, not just rendered text),
# so the console can render clean tone/lexicon/claims chips and link the source.
class BrandVoiceDoc(_Camel):
    tenant_id: str
    tone: list[str] = []
    structure: list[str] = []
    prefer: list[str] = []
    ban: list[str] = []
    approved_claims: list[str] = []
    source: str = ""


# --------------------------------------------------------------------------- #
# Grounding parsing — the draft step's audit list is the source of truth for
# "what THIS draft was allowed to use". Pure string parsing, no inference.
# --------------------------------------------------------------------------- #
def _parse_grounding(grounding: list[Any]) -> dict[str, Any]:
    """Split the per-draft ``grounding`` audit list into typed facts.

    Entries are emitted by ``build_outreach_draft`` as ``key=value`` (e.g.
    ``name=Rae``, ``city=Austin``, ``brand_voice=ladies8391``, ``copy=...``) and
    ``research:<url>``. Unknown entries are kept verbatim in ``raw`` so nothing is
    silently dropped."""
    out: dict[str, Any] = {
        "name": None, "city": None, "note": None, "interest": None,
        "lifecycle": None, "last_tattoo_style": None, "win_back": False,
        "brand_voice_tenant": None, "research_urls": [], "copy": None, "raw": [],
    }
    for g in grounding or []:
        s = str(g).strip()
        if s.startswith("research:"):
            url = s[len("research:"):].strip()
            if url:
                out["research_urls"].append(url)
            continue
        out["raw"].append(s)
        key, _, val = s.partition("=")
        key = key.strip()
        val = val.strip()
        if key == "name":
            out["name"] = val or None
        elif key == "city":
            out["city"] = val or None
        elif key == "note":
            out["note"] = val or None
        elif key in ("interest/aesthetic", "interest"):
            out["interest"] = val or None
        elif key == "lifecycle":
            out["lifecycle"] = val or None
        elif key == "last_tattoo_style":
            out["last_tattoo_style"] = val or None
        elif key == "win_back_candidate":
            out["win_back"] = val.lower() in ("true", "1", "yes")
        elif key == "brand_voice":
            out["brand_voice_tenant"] = val or None
        elif key == "copy":
            out["copy"] = val or None
    return out


def _as_dict(v: Any) -> dict[str, Any]:
    """Coerce a JSONB column (already a dict, or a JSON string) to a dict; {} on
    anything else. Defensive — agent_runs.input/output arrive as either shape."""
    if isinstance(v, dict):
        return v
    if isinstance(v, str):
        try:
            d = json.loads(v)
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}
    return {}


def _cust_id_from_action(action: dict[str, Any]) -> str | None:
    """The lead this draft targets, parsed from ``idempotency_key`` = ``"{run_id}:{cust}"``
    (the per-lead outreach convention). None when the key isn't lead-scoped."""
    key = action.get("idempotency_key") or ""
    run_id = action.get("run_id") or ""
    if run_id and key.startswith(run_id + ":"):
        return key[len(run_id) + 1:] or None
    # Fallback: a trailing ``cust_*`` segment.
    if ":" in key:
        tail = key.rsplit(":", 1)[-1]
        if tail.startswith("cust"):
            return tail
    return None


def _pick_step(
    agent_runs: list[dict[str, Any]], role: str, *, cust_id: str | None, channel: str | None
) -> dict[str, Any] | None:
    """The agent_runs row of ``role`` that produced THIS draft. Prefer an exact
    per-lead match (``input.customer_id == cust_id``); else a channel match; else the
    sole row of that role; else None — never a guessed cross-lead step."""
    rows = [r for r in agent_runs if str(r.get("role") or "").lower() == role]
    if not rows:
        return None
    if cust_id:
        for r in rows:
            if str(_as_dict(r.get("input")).get("customer_id") or "") == cust_id:
                return r
    if channel:
        for r in rows:
            if str(_as_dict(r.get("input")).get("channel") or "").lower() == str(channel).lower():
                return r
    if len(rows) == 1:
        return rows[0]
    # Multiple rows, no confident link: do not guess a cross-lead step.
    return None


def _summarize_draft(draft_out: dict[str, Any]) -> str | None:
    hook = draft_out.get("hook") or draft_out.get("headline") or ""
    cta = draft_out.get("call_to_action") or draft_out.get("cta") or ""
    cap = draft_out.get("caption") or ""
    bits = [b for b in (str(hook), (f"CTA: {cta}" if cta else "")) if b]
    summary = " | ".join(bits).strip()
    return (summary or str(cap)[:160]) or None


# --------------------------------------------------------------------------- #
# Pure assembler — the real-only logic, unit-tested without a database.
# --------------------------------------------------------------------------- #
def assemble_action_evidence(
    *,
    action: dict[str, Any],
    agent_runs: list[dict[str, Any]] | None = None,
    research_sources: list[dict[str, Any]] | None = None,
    memories: list[dict[str, Any]] | None = None,
    brand_voice: BrandVoiceDoc | None = None,
    internal_notes: str | None = None,
    campaign_id: str | None = None,
    reasoning_url: str | None = None,
) -> ActionEvidence:
    """Assemble the real-only :class:`ActionEvidence` for one staged draft from
    already-fetched rows. A category is populated ONLY when a real row backs it;
    otherwise it is omitted (None / empty list)."""
    agent_runs = agent_runs or []
    research_sources = research_sources or []
    memories = memories or []

    run_id = action.get("run_id")
    cust_id = _cust_id_from_action(action)
    channel = action.get("channel")

    draft_step = _pick_step(agent_runs, "draft", cust_id=cust_id, channel=channel)
    researcher_step = _pick_step(agent_runs, "researcher", cust_id=cust_id, channel=channel)
    # critic / jury are run-level (not per-lead in the studio path).
    critic_step = next((r for r in agent_runs if str(r.get("role") or "").lower() == "critic"), None)
    jury_step = next((r for r in agent_runs if str(r.get("role") or "").lower() == "jury"), None)

    draft_out = _as_dict(draft_step.get("output")) if draft_step else {}
    draft_in = _as_dict(draft_step.get("input")) if draft_step else {}
    research_out = _as_dict(researcher_step.get("output")) if researcher_step else {}
    parsed = _parse_grounding(draft_out.get("grounding") or [])

    ev = ActionEvidence(
        action_id=str(action.get("id")),
        run_id=run_id,
        campaign_id=campaign_id,
        tenant_id=str(action.get("tenant_id") or ""),
        channel=channel,
        target=action.get("target"),
        status=action.get("status"),
        confidence=action.get("conf"),
        threshold=action.get("threshold"),
        confidence_reason=action.get("esc_label"),
        reasoning_url=reasoning_url,
    )

    # --- Producing agent + its reasoning ------------------------------------ #
    if draft_step:
        ev.created_by = EvidenceAgent(
            role=draft_step.get("role"),
            model=draft_step.get("model"),
            reasoning_summary=_summarize_draft(draft_out),
        )

    # --- Brand voice: shown ONLY when the draft genuinely wrote in it -------- #
    # Proof = a ``brand_voice=`` grounding marker (outreach path) OR the content
    # path's persisted ``brand_voice_applied`` input flag. Never shown otherwise.
    voice_used = bool(parsed["brand_voice_tenant"]) or bool(draft_in.get("brand_voice_applied"))
    if voice_used and brand_voice is not None:
        ev.brand_voice = EvidenceBrandVoice(
            tenant_id=brand_voice.tenant_id,
            used=True,
            tone=brand_voice.tone,
            structure=brand_voice.structure,
            prefer=brand_voice.prefer,
            ban=brand_voice.ban,
            approved_claims=brand_voice.approved_claims,
            source=brand_voice.source,
        )

    # --- Customer / CSV facts the copy was allowed to use ------------------- #
    db_history = _as_dict(research_out.get("db_history"))
    facts_used = list(parsed["raw"])  # the verbatim audit list (minus research urls)
    has_customer = any([
        cust_id, parsed["name"], parsed["city"], parsed["note"], parsed["interest"],
        parsed["lifecycle"], parsed["last_tattoo_style"], db_history,
    ])
    if has_customer:
        ev.customer = EvidenceCustomer(
            customer_id=cust_id or draft_in.get("customer_id"),
            name=parsed["name"] or research_out.get("lead"),
            city=parsed["city"] or db_history.get("city"),
            note=parsed["note"],
            interest=parsed["interest"],
            lifecycle=parsed["lifecycle"] or db_history.get("lifecycle"),
            last_tattoo_style=parsed["last_tattoo_style"],
            win_back_candidate=bool(parsed["win_back"] or db_history.get("win_back_candidate")),
            facts_used=facts_used,
        )

    # --- Lead memories: real rows for this lead ----------------------------- #
    for m in memories:
        md = _as_dict(m.get("metadata"))
        ev.lead_memories.append(EvidenceMemory(
            text=str(m.get("text") or ""),
            kind=md.get("kind"),
            created_at=str(m.get("created_at")) if m.get("created_at") else None,
        ))

    # --- Internal notes (operator brand/strategy notes attached to the plan) - #
    if internal_notes and internal_notes.strip():
        ev.internal_notes = internal_notes.strip()

    # --- Research sources: ONLY the URLs THIS draft cited (real-only) -------- #
    # Enrich title/snippet/query from the run's real researcher.sources and the
    # research_sources rows — never invent a title for a url the draft didn't use.
    enrich: dict[str, dict[str, Any]] = {}
    for s in research_out.get("sources") or []:
        sd = _as_dict(s) if not isinstance(s, dict) else s
        u = (sd.get("url") or "").strip()
        if u:
            enrich[u] = {"title": sd.get("title"), "snippet": sd.get("snippet"), "query": sd.get("query")}
    for r in research_sources:
        u = (r.get("url") or "").strip()
        if u:
            enrich.setdefault(u, {})
            enrich[u] = {
                "title": r.get("title") or enrich[u].get("title"),
                "snippet": r.get("snippet") or enrich[u].get("snippet"),
                "query": r.get("query") or enrich[u].get("query"),
            }

    # A per-lead draft carries a ``grounding`` audit list — its ``research:<url>``
    # entries are the EXACT (possibly empty) set it used, so an empty set means "used
    # none" and we show none. A content-path draft (or a missing draft step) has no
    # grounding audit, so we fall back to the run's real cited ``research_sources``.
    has_grounding = isinstance(draft_out.get("grounding"), list)
    if has_grounding:
        cited = parsed["research_urls"]
    else:
        cited = [r.get("url") for r in research_sources if (r.get("url") or "").strip()]
    seen: set[str] = set()
    for u in cited:
        u = (u or "").strip()
        if not u or u in seen:
            continue
        seen.add(u)
        meta = enrich.get(u, {})
        ev.research_sources.append(EvidenceResearchSource(
            url=u, title=meta.get("title"), snippet=meta.get("snippet"), query=meta.get("query"),
        ))

    # --- Tool calls actually invoked ---------------------------------------- #
    copy = parsed["copy"]
    if copy:
        if copy == "copywriter_email_cell":
            ev.tool_calls.append(EvidenceToolCall(name="copywriter_email_cell", detail="brand-voiced email copy"))
        else:
            ev.tool_calls.append(EvidenceToolCall(name=copy))
    elif draft_step and draft_step.get("model"):
        ev.tool_calls.append(EvidenceToolCall(name="content_brief_cell", detail=str(draft_step.get("model"))))
    if researcher_step:
        n_cited = research_out.get("cited")
        ev.tool_calls.append(EvidenceToolCall(
            name="firecrawl_search",
            detail=(f"{n_cited} source(s) cited" if n_cited is not None else None),
        ))
        if db_history:
            ev.tool_calls.append(EvidenceToolCall(name="customer_db", detail="DB lead history"))

    # --- Critic verdict (independent quality pass) -------------------------- #
    if critic_step:
        co = _as_dict(critic_step.get("output"))
        ev.critic_review = EvidenceCritic(
            verdict=co.get("verdict"),
            rationale=co.get("rationale"),
            model=critic_step.get("model"),
        )

    # --- Jury score --------------------------------------------------------- #
    if jury_step:
        jo = _as_dict(jury_step.get("output"))
        agg = jo.get("aggregate")
        ev.jury = EvidenceJury(
            aggregate=float(agg) if isinstance(agg, (int, float)) else None,
            decision=jo.get("decision"),
            note=jo.get("note"),
        )

    return ev


# --------------------------------------------------------------------------- #
# DB resolution helpers + the thin wrapper.
# --------------------------------------------------------------------------- #
def resolve_brand_voice_doc(tenant_id: str | None) -> BrandVoiceDoc | None:
    """Load the tenant's structured brand-voice dimensions (tone / structure /
    lexicon / bans / approved claims) + the brand-dna source path, for the console
    to render as clean chips. None (honest) when the pack can't resolve."""
    from studio.customer_research import _DEFAULT_TENANT

    tid = tenant_id or _DEFAULT_TENANT
    try:
        from config.loader import load_pack
        from kb.voice import load_voice_dimensions

        pack = load_pack(tid)
        dims = load_voice_dimensions(pack)
    except Exception:
        # Retry with the default tenant so a placeholder deps tenant still resolves.
        if tid != _DEFAULT_TENANT:
            return resolve_brand_voice_doc(_DEFAULT_TENANT)
        return None
    v = dims.vocabulary
    family, _, tslug = (pack.voice.skill or "").partition("/")
    source = (
        f"skills/{family}/tenants/{tslug}/brand-dna.md" if family and tslug else f"pack:{tid}"
    )
    return BrandVoiceDoc(
        tenant_id=tid,
        tone=list(dims.tone),
        structure=list(dims.structure),
        prefer=list(v.prefer),
        ban=list(v.ban),
        approved_claims=list(v.approved_claims),
        source=source,
    )


def build_action_evidence(action_id: str, *, dsn: str | None = None) -> ActionEvidence | None:
    """Fetch the real rows for ``action_id`` and assemble its evidence. Returns None
    when the action does not exist. Best-effort per category: a store that is
    unreachable degrades that category to empty, never to a fabricated value."""
    from actions.store import get_action

    row = get_action(action_id, dsn=dsn)
    if row is None:
        return None
    action: dict[str, Any] = {
        "id": row.id, "run_id": row.run_id, "tenant_id": row.tenant_id,
        "channel": row.channel, "target": row.target, "status": row.status,
        "conf": row.conf, "threshold": row.threshold, "esc_label": row.esc_label,
        "idempotency_key": row.idempotency_key, "decision_id": row.decision_id,
    }
    run_id = row.run_id
    cust_id = _cust_id_from_action(action)

    agent_runs: list[dict[str, Any]] = []
    campaign_id: str | None = None
    if run_id:
        try:
            from team.store import TeamStore

            ts = TeamStore(dsn) if dsn else TeamStore(_default_dsn())
            agent_runs = ts.list_agent_runs(run_id)
            campaign_id = next(
                (str(a.get("campaign_id")) for a in agent_runs if a.get("campaign_id")), None
            )
        except Exception:
            agent_runs = []

    research_sources: list[dict[str, Any]] = []
    if run_id:
        try:
            from research.sources_store import list_sources

            research_sources = list_sources(run_id, dsn=dsn)
        except Exception:
            research_sources = []

    memories: list[dict[str, Any]] = []
    if cust_id:
        try:
            from memory import MemoryStore

            store = MemoryStore(dsn=dsn)
            for m in store.list_for_subject(
                tenant_id=row.tenant_id, subject_type="customer", subject_id=cust_id
            ):
                memories.append({
                    "text": m.text, "metadata": m.metadata,
                    "created_at": getattr(m, "created_at", None),
                })
        except Exception:
            memories = []

    internal_notes = _resolve_internal_notes(action_id, memories, dsn)
    brand_voice = resolve_brand_voice_doc(row.tenant_id)

    reasoning_url: str | None = None
    if run_id:
        try:
            import observability

            reasoning_url = observability.trace_url(run_id)
        except Exception:
            reasoning_url = None

    return assemble_action_evidence(
        action=action,
        agent_runs=agent_runs,
        research_sources=research_sources,
        memories=memories,
        brand_voice=brand_voice,
        internal_notes=internal_notes,
        campaign_id=campaign_id,
        reasoning_url=reasoning_url,
    )


def _resolve_internal_notes(
    action_id: str, memories: list[dict[str, Any]], dsn: str | None
) -> str | None:
    """The operator brand/strategy notes attached to the session that staged this
    draft, if any. Found via the staging memory (it carries session_id) -> the
    persisted plan's ``notes``. Best-effort + real-only: None when not resolvable."""
    session_id: str | None = None
    for m in memories:
        md = m.get("metadata") if isinstance(m.get("metadata"), dict) else {}
        if md.get("action_id") == action_id and md.get("session_id"):
            session_id = str(md.get("session_id"))
            break
    if not session_id:
        return None
    try:
        from studio.campaign_plan_store import latest_plans

        rows = latest_plans(1, session_id=session_id, dsn=dsn)
        if not rows:
            return None
        state = rows[0].get("state") if isinstance(rows[0], dict) else None
        notes = state.get("notes") if isinstance(state, dict) else None
        return notes if (notes and str(notes).strip()) else None
    except Exception:
        return None


def _default_dsn() -> str:
    import os

    return os.environ.get("ENGINE_DATABASE_URL") or "postgresql://scalers:scalers@localhost:5432/scalers"
