"""First-class per-lead DOSSIER (P2-C, bead CustomerAcq-65w.7).

The per-lead run loop (:func:`studio.agui._execute_provided_leads_sync`) already gathers
dossier-*worth* of real facts for every lead — DB identity/contact, persona traits, tattoo
history, a grounded psych ``profile`` (objection + evidence), verified research, the chosen
angle, and the resolved CTA. What it lacked was a NAMED, evidence-linked record tying those
together so each staged draft can deep-link to "here is exactly what we knew about this lead
and where every claim came from".

:class:`Dossier` is that record. :func:`build_dossier` is a PURE assembler: it reads ONLY
the real inputs the loop already holds and never invents a value. Every field is a
:class:`DossierField` carrying its ``value`` plus a ``confidence`` and a ``source`` string
that traces to the real origin (``db:``/``csv:``/``persona:``/``analyst:``/``research:``/
``angle:``), or is honestly empty (``confidence="none"``, ``source="none"``).

HONESTY (mirrors the whole spine): thin data does NOT become fabricated personalization.
When the lead has no distinguishing high-confidence signal, ``limited_personalization`` is
True and ``personalization_note`` says so plainly — the draft then stays a brand-safe warm
generic angle rather than inventing specifics (A7/A8 of the upgrade design).

Nothing here sends, loads a skill, or calls a model. Pure projection of already-real fields.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Confidence = Literal["high", "medium", "low", "none"]


class DossierField(BaseModel):
    """One dossier field with per-field provenance.

    ``confidence`` grades how hard the signal is (``high`` = a stated/first-party DB/CSV
    fact; ``medium`` = a persona-inferred impression; ``low`` = a weak/derived hint;
    ``none`` = honestly empty). ``source`` names the real origin so the operator can
    verify it (e.g. ``"db:customers.email"``, ``"analyst:primary_objection(stated)"``)."""

    value: Any = None
    confidence: Confidence = "none"
    source: str = "none"

    @property
    def present(self) -> bool:
        return self.value not in (None, "", [], {}) and self.confidence != "none"


def _field(value: Any, confidence: Confidence, source: str) -> DossierField:
    """A present field, or an honest-empty one when ``value`` is blank (never fabricated)."""
    if value in (None, "", [], {}):
        return DossierField(value=None, confidence="none", source="none")
    return DossierField(value=value, confidence=confidence, source=source)


class Dossier(BaseModel):
    """The evidence-linked per-lead record a staged draft points back to.

    Every field traces to a REAL source or is honestly empty. ``limited_personalization``
    is the honesty flag: when the lead is thin, we say so instead of inventing specifics."""

    customer_id: str | None = None
    run_id: str | None = None

    # Identity + contact
    name: DossierField = Field(default_factory=DossierField)
    email: DossierField = Field(default_factory=DossierField)
    phone: DossierField = Field(default_factory=DossierField)
    social_handle: DossierField = Field(default_factory=DossierField)

    # Source + lifecycle (spec §8)
    source: DossierField = Field(default_factory=DossierField)
    lead_stage: DossierField = Field(default_factory=DossierField)

    # Segmentation + interest
    customer_type: DossierField = Field(default_factory=DossierField)
    tattoo_interest: DossierField = Field(default_factory=DossierField)
    known_interests: list[str] = Field(default_factory=list)
    tattoo_style_preference: DossierField = Field(default_factory=DossierField)
    artist_style_match: DossierField = Field(default_factory=DossierField)

    # Behavioral / psych read (grounded)
    conversation_summary: DossierField = Field(default_factory=DossierField)
    likely_objection: DossierField = Field(default_factory=DossierField)
    objection_evidence: str = ""
    public_research_summary: DossierField = Field(default_factory=DossierField)

    # Strategy the draft leads with
    best_angle: DossierField = Field(default_factory=DossierField)
    recommended_cta: DossierField = Field(default_factory=DossierField)

    # The full audit list of what the copy was allowed to use (the draft grounding).
    evidence_used: list[str] = Field(default_factory=list)

    # Honest personalization posture (A8 / spec §8): low confidence -> say so, do not
    # invent. ``personalization_level`` is the spec's headline grade; ``missing_data``
    # NAMES every §8 field that is genuinely absent for this lead (the "say missing"
    # contract — never a fabricated depth).
    limited_personalization: bool = False
    personalization_note: str = ""
    personalization_level: str = "low"  # low | medium | high
    missing_data: list[str] = Field(default_factory=list)

    def linked_fields(self) -> dict[str, DossierField]:
        """The provenance-carrying fields, keyed by name — for the evidence panel."""
        return {
            "name": self.name, "email": self.email, "phone": self.phone,
            "social_handle": self.social_handle, "customer_type": self.customer_type,
            "tattoo_interest": self.tattoo_interest,
            "artist_style_match": self.artist_style_match,
            "conversation_summary": self.conversation_summary,
            "likely_objection": self.likely_objection, "best_angle": self.best_angle,
            "recommended_cta": self.recommended_cta,
        }


def _pf(profile: Any, field: str) -> tuple[str, str, str]:
    """Read ``(value, signal, evidence)`` from a PsychProfile field, tolerating a pydantic
    object OR a plain dict (the same tolerant reader shape as customer_research)."""
    f = getattr(profile, field, None)
    if f is None and isinstance(profile, dict):
        f = profile.get(field)
    if f is None:
        return "", "", ""
    if isinstance(f, dict):
        return (str(f.get("value") or ""), str(f.get("signal") or ""),
                str(f.get("evidence") or ""))
    return (str(getattr(f, "value", "") or ""), str(getattr(f, "signal", "") or ""),
            str(getattr(f, "evidence", "") or ""))


def _profile_attr(profile: Any, name: str, default: str = "") -> str:
    if profile is None:
        return default
    if isinstance(profile, dict):
        return str(profile.get(name) or default)
    return str(getattr(profile, name, default) or default)


def build_dossier(
    facts: dict[str, Any],
    *,
    profile: Any = None,
    angle: dict[str, Any] | None = None,
    offer: Any = None,
    research: list[dict[str, Any]] | None = None,
    channel: str | None = None,
    cta_kind: str | None = None,
    evidence_used: list[str] | None = None,
    run_id: str | None = None,
) -> Dossier:
    """Assemble a :class:`Dossier` from the REAL facts the loop already holds. Pure — no DB,
    no model, no skill load. Every field traces to a real source or is honestly empty.

    ``facts`` — the grounded DB/CSV lead facts (``lookup_lead`` shape).
    ``profile`` — the grounded :class:`~studio.psych_profile.PsychProfile` (or None).
    ``angle`` — the chosen angle dict (``build_outreach_draft`` returns label/key/generic/
    inferred + the ``why_different`` rationale); drives ``best_angle`` + the honesty flag.
    ``offer`` — a REAL substantiated offer (or None); only ever referenced, never invented.
    ``research`` — verified web hits about this lead (or []).
    ``channel`` / ``cta_kind`` — the resolved outreach channel + CTA form.
    ``evidence_used`` — the draft's grounding audit list (what the copy could use)."""
    traits = facts.get("persona_traits", {}) or {}
    interests = facts.get("interests", []) or []
    tattoos = facts.get("tattoo_history", []) or []
    last_style = tattoos[0]["style"] if tattoos and tattoos[0].get("style") else None

    # --- identity + contact (hard first-party facts) ------------------------------- #
    name = _field(facts.get("name"), "high", "db:customers.name")
    email = _field(facts.get("email"), "high", "db:customers.email")
    phone = _field(facts.get("phone"), "high", "db:customers.phone")
    social = _field(facts.get("ig_handle"), "high", "db:customers.ig_handle")

    # --- segment (real column first, else persona lifecycle as an inferred read) ---- #
    seg_val = facts.get("customer_type") or facts.get("lead_stage")
    if seg_val:
        customer_type = _field(seg_val, "high", "db:customers.customer_type")
    else:
        lifecycle = traits.get("lifecycle_stage")
        customer_type = _field(lifecycle, "medium", "persona:lifecycle_stage")

    # --- interest (CSV interest is hard; persona aesthetic lean is inferred) -------- #
    if interests:
        tattoo_interest = _field(interests[0], "high", "db:customers.interests")
    elif traits.get("aesthetic_lean"):
        tattoo_interest = _field(traits.get("aesthetic_lean"), "medium",
                                 "persona:aesthetic_lean")
    elif last_style:
        tattoo_interest = _field(last_style, "high", "db:tattoo_history.style")
    else:
        tattoo_interest = _field(None, "none", "none")

    # --- artist / style match (real artist on file + our own past work) ------------- #
    artist = (facts.get("artist") or "").strip()
    if artist and last_style:
        artist_style = _field(f"{artist} — past {last_style} work on file", "high",
                              "db:customers.artist+tattoo_history.style")
    elif artist:
        artist_style = _field(artist, "high", "db:customers.artist")
    elif last_style:
        artist_style = _field(f"past {last_style} piece on file", "high",
                             "db:tattoo_history.style")
    else:
        artist_style = _field(None, "none", "none")

    # --- conversation summary (grounded psych read only) ---------------------------- #
    had_conv = bool(_profile_attr(profile, "had_conversation", "")) if profile is not None else False
    if profile is not None and had_conv:
        where = _profile_attr(profile, "where_customer_sits")
        summary = where or "has prior conversation history with the studio"
        conversation_summary = _field(summary, "high", "analyst:where_customer_sits")
    else:
        conversation_summary = _field(None, "none", "none")

    # --- likely objection (verbatim-grounded, honest signal grading) ---------------- #
    objection_evidence = ""
    if profile is not None:
        obj_val, obj_sig, obj_ev = _pf(profile, "primary_objection")
        if obj_val and obj_val != "none-found" and obj_sig in ("stated", "inferred"):
            conf: Confidence = "high" if obj_sig == "stated" else "medium"
            likely_objection = _field(obj_val, conf, f"analyst:primary_objection({obj_sig})")
            objection_evidence = obj_ev
        else:
            likely_objection = _field(None, "none", "none")
    else:
        likely_objection = _field(None, "none", "none")

    # --- best angle the draft leads with (from the chosen angle) -------------------- #
    angle = angle or {}
    angle_label = angle.get("label")
    angle_generic = bool(angle.get("generic"))
    angle_inferred = bool(angle.get("inferred"))
    if angle_label and not angle_generic:
        a_conf: Confidence = "medium" if angle_inferred else "high"
        best_angle = _field(angle_label, a_conf, f"angle:{angle.get('key') or 'chosen'}")
    elif angle_label:
        best_angle = _field(angle_label, "low", "angle:generic-honest")
    else:
        best_angle = _field(None, "none", "none")
    if offer is not None and getattr(offer, "code", None):
        # A real substantiated offer strengthens the angle's basis (never invented).
        best_angle.source = f"{best_angle.source}+offer:{offer.code}"

    # --- recommended CTA (deterministic; the send-safe next step) -------------------- #
    if cta_kind == "booking-link":
        recommended_cta = _field("Point them to the real booking link", "high",
                                 "cta:booking-link")
    elif cta_kind == "reply-based" or channel in ("gmail", "email"):
        recommended_cta = _field("Invite a reply to send a booking link", "high",
                                 "cta:reply-based")
    else:
        recommended_cta = _field("Open a genuine reply on the chosen channel", "medium",
                                 f"cta:{channel or 'channel'}")

    # --- honest personalization posture --------------------------------------------- #
    # Count the HARD (high-confidence) distinguishing signals. A lead with none of these is
    # thin: we flag it and keep the angle brand-safe/generic rather than fake specifics.
    distinguishing = [
        f for f in (tattoo_interest, artist_style, conversation_summary,
                    likely_objection, social)
        if f.confidence == "high"
    ]
    limited = angle_generic or (not distinguishing)
    if angle_generic:
        note = ("No distinguishing research or history on file — the draft stays an honest, "
                "warm general introduction rather than manufacturing specifics.")
    elif not distinguishing:
        soft = [f.source.split(":", 1)[-1] for f in (tattoo_interest, customer_type)
                if f.confidence == "medium"]
        note = ("Thin data: personalization rests on "
                + (", ".join(soft) if soft else "name/segment only")
                + " (inferred/soft signals). Kept brand-safe and honest; no invented facts.")
    else:
        note = ""

    # --- spec §8 extras: source / lead_stage / interests / style pref / research ----- #
    source_f = _field(facts.get("source"), "high", "db:customers.source")
    lead_stage_f = _field(facts.get("lead_stage"), "high", "db:customers.lead_stage")
    known_interests = [str(i) for i in interests if i]
    if last_style:
        tattoo_style_pref = _field(last_style, "high", "db:tattoo_history.style")
    elif traits.get("aesthetic_lean"):
        tattoo_style_pref = _field(traits.get("aesthetic_lean"), "medium",
                                   "persona:aesthetic_lean")
    else:
        tattoo_style_pref = _field(None, "none", "none")
    n_research = sum(1 for r in (research or []) if (r.get("url") or "").strip())
    research_summary = (
        _field(f"{n_research} verified public source(s) on file", "high",
               "research:web")
        if n_research else _field(None, "none", "none")
    )

    # --- MISSING-DATA contract (spec §8: "if data is missing, say missing") ---------- #
    # Name every §8 field that is genuinely absent for this lead, so the dossier states
    # its gaps plainly instead of implying a depth it does not have.
    _checks: list[tuple[str, bool]] = [
        ("name", name.present), ("email", email.present), ("phone", phone.present),
        ("social_handle", social.present), ("source", source_f.present),
        ("lead_stage", lead_stage_f.present), ("customer_type", customer_type.present),
        ("known_interests", bool(known_interests)),
        ("tattoo_style_preference", tattoo_style_pref.present),
        ("conversation_summary", conversation_summary.present),
        ("known_objections", likely_objection.present),
        ("artist_affinity", artist_style.present),
        ("public_research_summary", research_summary.present),
    ]
    missing_data = [name_ for name_, present in _checks if not present]

    # Personalization LEVEL (spec headline): high = a real conversation or research read
    # to ground a personal message; medium = hard interest/style/artist signal; low =
    # name/segment only (safe generic campaign, never a fake-personal message).
    if conversation_summary.present or research_summary.present:
        personalization_level = "high"
    elif any(f.confidence == "high" for f in
             (tattoo_interest, artist_style, tattoo_style_pref, social)):
        personalization_level = "medium"
    else:
        personalization_level = "low"

    return Dossier(
        customer_id=facts.get("customer_id"),
        run_id=run_id,
        name=name, email=email, phone=phone, social_handle=social,
        source=source_f, lead_stage=lead_stage_f,
        customer_type=customer_type, tattoo_interest=tattoo_interest,
        known_interests=known_interests, tattoo_style_preference=tattoo_style_pref,
        artist_style_match=artist_style,
        conversation_summary=conversation_summary,
        likely_objection=likely_objection, objection_evidence=objection_evidence,
        public_research_summary=research_summary,
        best_angle=best_angle, recommended_cta=recommended_cta,
        evidence_used=list(evidence_used or []),
        limited_personalization=limited, personalization_note=note,
        personalization_level=personalization_level, missing_data=missing_data,
    )


def build_customer_dossier(
    tenant_id: str, customer_id: str, *, dsn: str | None = None,
    use_llm: bool = False, research: list[dict[str, Any]] | None = None,
) -> "Dossier | None":
    """Build a customer's dossier ON DEMAND from durable DB state (spec §8, nmh.6) — for
    "open a customer -> see their dossier". Returns ``None`` when the customer does not
    exist (honest, never a fabricated record).

    Reads REAL persistent state only: the grounded lead facts + persona + tattoo history
    (:func:`customer_research.lookup_lead`), the stored conversation
    (:func:`conversations.get_conversation`), and the grounded psych read
    (:func:`psych_profile.analyze_customer`). Because every source is durable, the
    dossier is identical after an engine restart. ``use_llm`` defaults False (the
    deterministic psych floor) so this is cheap + hermetic; no sends, no skill load."""
    from studio.conversations import get_conversation
    from studio.customer_research import lookup_lead
    from studio.psych_profile import analyze_customer

    facts = lookup_lead(tenant_id, customer_id=customer_id, dsn=dsn)
    if facts is None:
        return None
    conv = get_conversation(tenant_id, customer_id, dsn=dsn)
    known_artists = [facts["artist"]] if facts.get("artist") else None
    try:
        profile = analyze_customer(facts, conv, known_artists=known_artists,
                                   use_llm=use_llm)
    except Exception:
        profile = None  # honest: no psych read rather than a crash
    return build_dossier(facts, profile=profile, research=research)
