"""Competitor TOP-PICKS + mid-run selection pause (IG competitor-intelligence flow).

When the Instagram channel plan turns competitor research on
(``plan.channel_plans["ig"]["competitor_research"]``), the engine:

  1. scores the operator-uploaded ``competitor_posts`` rows
     (:func:`studio.competitor_intel.score_posts` — deterministic, per-component
     evidence) and surfaces the TOP ``k`` posts best-first, each with its
     verbatim caption + real metrics + persisted why-it-worked;
  2. if the run has NO recorded choice yet, persists an ``awaiting`` row in
     ``competitor_selections`` (durable — survives restarts) and the run PAUSES
     with a ``competitor_pick`` selection request before any molding/drafting;
  3. ``POST /studio/campaign/{run_id}/select-competitor`` records the pick
     (``selected``, the chosen option snapshotted in ``choice``) and re-invokes
     the executor, which finds the choice here and proceeds — the durable
     replay-skip prevents re-drafting;
  4. if NO competitor posts are on file the run does NOT pause: with
     ``competitor_research`` on the gate first attempts ONE bounded LIVE
     discovery pass (:func:`studio.competitor_discovery.run_discovery` —
     ToS-compliant: Firecrawl public-web search + Meta's OFFICIAL Business
     Discovery API only, never logged-in scraping) and pauses over the freshly
     scored posts when it lands anything; a failed/keyless/empty discovery
     returns ``('skip', note)`` with the honest note and the normal IG path
     continues.

The chosen post is a MOLD REFERENCE, never material to copy (hard product/safety
rule): :func:`mold_competitor_pattern` deconstructs its SHAPE
(:func:`studio.competitor_intel.deconstruct_caption`) and writes the
brand-adapted angle/hook/CTA direction from OUR proven brand patterns + the
tenant pack's voice — with a deterministic verbatim guard
(:func:`copies_verbatim`) so no competitor sentence survives into the molded
output, cell-refined or not.

HONESTY: options come only from real ``competitor_posts`` rows — operator
uploads or official-API live discovery, each labeled with its ``source``
(never scraping); every score/why is the persisted deterministic breakdown; an
empty table yields a 'skip' with a visible note, never a fabricated post or
metric.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

_DEFAULT_DSN = "postgresql://scalers:scalers@localhost:5432/scalers"

# The honest step note when the tenant has no competitor data (spec: never pause
# on an empty table; never fabricate posts or metrics).
NO_COMPETITOR_NOTE = "no competitor posts on file — upload the competitor export"

# How many top-scored posts pause #1 surfaces at most.
MAX_COMPETITOR_OPTIONS = 6

_SELECTIONS_SQL = (
    Path(__file__).resolve().parents[2] / "infra" / "initdb" / "28-competitor-selections.sql"
)


def _dsn(dsn: str | None = None) -> str:
    return dsn or os.environ.get("ENGINE_DATABASE_URL") or _DEFAULT_DSN


def _connect(dsn: str | None = None):
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(_dsn(dsn), autocommit=True, row_factory=dict_row)


def ensure_schema(dsn: str | None = None) -> None:
    """Apply ``28-competitor-selections.sql`` (idempotent)."""
    with _connect(dsn) as conn:
        conn.execute(_SELECTIONS_SQL.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# The plan contract seam (another builder owns the field; code against it).
# --------------------------------------------------------------------------- #
def social_channel_plan(plan: Any, channel: str) -> dict[str, Any]:
    """The plan's block for a SOCIAL channel (``plan.channel_plans[channel]``) — keys:
    ``competitor_research`` (bool), ``attach_images`` (bool), ``image_style`` (str).

    A Facebook page post is the SAME artefact as an Instagram post — an image with a
    caption — so both legs read their own block through one accessor. Facebook used to
    be excluded from the competitor + artwork gates entirely, which is why an 'fb'
    campaign emitted email-shaped body text with no image attached. Honest-empty ``{}``
    when unset, so an untouched plan keeps today's behavior. Pure."""
    plans = getattr(plan, "channel_plans", {}) or {}
    if not isinstance(plans, dict):
        return {}
    # The interview writes 'ig'/'fb' on one path and 'instagram'/'facebook' on another.
    # A block keyed one way must still be found when the leg launches under the other,
    # or the operator's competitor_research / attach_images answers go silently missing
    # and the post ships with no image.
    aliases = {
        "ig": ("ig", "instagram"),
        "instagram": ("instagram", "ig"),
        "fb": ("fb", "facebook"),
        "facebook": ("facebook", "fb"),
    }
    key = str(channel or "").strip().lower()
    for name in aliases.get(key, (key,)):
        block = plans.get(name)
        if isinstance(block, dict) and block:
            return block
    return {}


def ig_channel_plan(plan: Any) -> dict[str, Any]:
    """The plan's Instagram channel plan (``plan.channel_plans["ig"]``). Thin alias of
    :func:`social_channel_plan` kept for existing callers. Pure."""
    return social_channel_plan(plan, "ig")


# --------------------------------------------------------------------------- #
# Top-k candidate ranking (persisted deterministic scores over real rows).
# --------------------------------------------------------------------------- #
def top_competitor_options(
    tenant_id: str,
    *,
    artist: str | None = None,
    k: int = MAX_COMPETITOR_OPTIONS,
    dsn: str | None = None,
) -> list[dict[str, Any]]:
    """The TOP ``k`` scored competitor posts for this tenant, best-first
    (:func:`studio.competitor_intel.score_posts` — highest total_score first,
    unscorable rows last), each carrying the operator-facing evidence:
    ``[{postId, handle, caption, url, metrics, totalScore, whyItWorked,
    visualTags, source}]`` (``source`` says where the row came from —
    ``'upload'`` or ``'discovery'``). The caption is VERBATIM here because the
    OPERATOR reviews the real post to pick a pattern — the drafter never sees
    it un-molded. ``[]`` when no competitor posts are on file (the caller
    skips honestly)."""
    from studio.competitor_intel import score_posts

    scored = score_posts(tenant_id, artist=artist, dsn=dsn)
    options: list[dict[str, Any]] = []
    for p in scored[: max(1, k)]:
        options.append(
            {
                "postId": p["id"],
                "handle": p.get("handle"),
                "caption": p.get("caption"),
                "url": p.get("url"),
                "metrics": dict(p.get("metrics") or {}),
                "totalScore": p.get("total_score"),
                "whyItWorked": p.get("why_it_worked"),
                "visualTags": [str(t) for t in (p.get("visual_tags") or [])],
                "source": p.get("source") or "upload",
            }
        )
    return options


def resolve_pick(
    tenant_id: str, post_id: str, *, dsn: str | None = None
) -> dict[str, Any] | None:
    """The selected competitor post's REAL row, re-read live from
    ``competitor_posts``: ``{postId, handle, caption, url, platform, metrics,
    totalScore, whyItWorked, visualTags}`` — or ``None`` when the post no longer
    exists for this tenant. Never fabricates a field."""
    if not post_id:
        return None
    try:
        from studio.competitor_intel import ensure_schema as ensure_posts_schema

        ensure_posts_schema(dsn)
        with _connect(dsn) as conn:
            row = conn.execute(
                "SELECT id, handle, url, platform, caption, visual_tags, metrics, "
                "total_score, why_it_worked FROM competitor_posts "
                "WHERE tenant_id=%s AND id=%s",
                (tenant_id, post_id),
            ).fetchone()
    except Exception:
        return None
    if row is None:
        return None
    total = row.get("total_score")
    return {
        "postId": row["id"],
        "handle": row.get("handle"),
        "caption": row.get("caption"),
        "url": row.get("url"),
        "platform": row.get("platform"),
        "metrics": dict(row.get("metrics") or {}),
        "totalScore": float(total) if total is not None else None,
        "whyItWorked": row.get("why_it_worked"),
        "visualTags": [str(t) for t in (row.get("visual_tags") or [])],
    }


# --------------------------------------------------------------------------- #
# Durable selection state (mirrors studio.artwork_flow exactly).
# --------------------------------------------------------------------------- #
def get_selection(run_id: str, *, dsn: str | None = None) -> dict[str, Any] | None:
    """The run's competitor-selection row (dict) or ``None``. Best-effort: an
    unreadable store reads as no selection (the run then proceeds as if un-gated
    — honest fallback, never a pause nobody can answer)."""
    try:
        ensure_schema(dsn)
        with _connect(dsn) as conn:
            row = conn.execute(
                "SELECT run_id, tenant_id, session_id, status, question, options, "
                "plan, choice FROM competitor_selections WHERE run_id=%s",
                (run_id,),
            ).fetchone()
    except Exception:
        return None
    return dict(row) if row else None


def request_selection(
    run_id: str,
    tenant_id: str,
    session_id: str | None,
    *,
    question: str,
    options: list[dict[str, Any]],
    plan_snapshot: dict[str, Any] | None = None,
    dsn: str | None = None,
) -> None:
    """Persist the ``awaiting`` selection row (idempotent upsert; a re-entrant
    pause refreshes the options, a ``selected`` row is left untouched)."""
    from psycopg.types.json import Json

    ensure_schema(dsn)
    with _connect(dsn) as conn:
        conn.execute(
            """
            INSERT INTO competitor_selections
                (run_id, tenant_id, session_id, status, question, options, plan)
            VALUES (%s,%s,%s,'awaiting',%s,%s,%s)
            ON CONFLICT (run_id) DO UPDATE SET
                question = EXCLUDED.question,
                options = EXCLUDED.options,
                plan = COALESCE(EXCLUDED.plan, competitor_selections.plan)
            WHERE competitor_selections.status = 'awaiting'
            """,
            (run_id, tenant_id, session_id, question, Json(options),
             Json(plan_snapshot) if plan_snapshot is not None else None),
        )


def record_choice(run_id: str, post_id: str, *, dsn: str | None = None) -> bool:
    """Record the operator's pick (awaiting → selected; the chosen option dict is
    snapshotted into ``choice``, ``resolved_at`` stamped). Returns False when the
    run has no awaiting selection (already selected, or never paused)."""
    from psycopg.types.json import Json

    ensure_schema(dsn)
    with _connect(dsn) as conn:
        row = conn.execute(
            "SELECT options FROM competitor_selections "
            "WHERE run_id=%s AND status='awaiting'",
            (run_id,),
        ).fetchone()
        if row is None:
            return False
        chosen = next(
            (o for o in (row.get("options") or [])
             if isinstance(o, dict) and str(o.get("postId")) == str(post_id)),
            None,
        ) or {"postId": post_id}
        done = conn.execute(
            "UPDATE competitor_selections SET status='selected', choice=%s, "
            "resolved_at=now() WHERE run_id=%s AND status='awaiting' "
            "RETURNING run_id",
            (Json(chosen), run_id),
        ).fetchone()
    return done is not None


def selection_request_payload(row: dict[str, Any]) -> dict[str, Any]:
    """The ``competitor_pick`` selection-request shape the run state exposes
    (``competitorSelectionRequest``, beside the artwork ``selectionRequest``):
    ``{"kind": "competitor_pick", "question": ..., "options": [...]}``."""
    return {
        "kind": "competitor_pick",
        "question": row.get("question") or "",
        "options": list(row.get("options") or []),
    }


# --------------------------------------------------------------------------- #
# The gate the IG executor calls (pause #1).
# --------------------------------------------------------------------------- #
def competitor_gate(
    run_id: str,
    tenant_id: str,
    session_id: str | None = None,
    plan: Any = None,
    *,
    artist: str | None = None,
    k: int = MAX_COMPETITOR_OPTIONS,
    dsn: str | None = None,
) -> tuple[str, Any]:
    """Decide the competitor-intelligence step for this run. Returns one of:

    * ``("continue", chosen_post)`` — a durable choice exists; mold THAT post's
      shape (never its words) into the run's brief;
    * ``("pause", selection_request)`` — scored options exist and no choice was
      made: the caller STOPS before molding/drafting and surfaces the request;
    * ``("skip", note)`` — no competitor posts on file (or the store failed):
      proceed on the normal IG path and record the honest note — never a pause
      the operator can't answer, never an invented post.

    When the table is EMPTY and the ig channel plan set ``competitor_research``,
    ONE bounded (~60s) LIVE discovery pass runs FIRST
    (:func:`studio.competitor_discovery.run_discovery` — ToS-compliant:
    Firecrawl public-web search + Meta's official Business Discovery API only).
    Whatever it lands is scored by the existing scorer and pauses normally
    (options carry ``source='discovery'`` and the payload an honest ``note``);
    a keyless/empty/failed discovery degrades to today's honest skip.
    """
    sel = get_selection(run_id, dsn=dsn)
    if sel and sel.get("status") == "selected":
        choice = sel.get("choice") or {}
        pick = resolve_pick(tenant_id, str(choice.get("postId") or ""), dsn=dsn)
        if pick is None and (choice.get("caption") or choice.get("handle")):
            # The live row is gone (re-import churn), but the recorded choice IS
            # the real option the operator picked from — still honest data.
            pick = dict(choice)
        if pick is not None:
            return "continue", pick
        return "skip", (
            "the selected competitor post is no longer on file — proceeding "
            "without competitor intel"
        )
    if sel and sel.get("status") == "awaiting":
        return "pause", selection_request_payload(sel)

    options = top_competitor_options(tenant_id, artist=artist, k=k, dsn=dsn)
    discovery_note: str | None = None
    if not options and bool(ig_channel_plan(plan).get("competitor_research")):
        # LIVE DISCOVERY (ToS-compliant, bounded ~60s): the operator turned
        # competitor research ON and nothing is on file — go find real posts via
        # Firecrawl public-web search + the official Business Discovery API,
        # then pause over them exactly like uploaded rows. Any failure (no keys,
        # no candidates, all misses) falls through to the honest skip below.
        try:
            from studio.competitor_discovery import run_discovery

            disc = run_discovery(tenant_id, plan=plan, dsn=dsn, time_budget_s=60.0)
            discovery_note = str(disc.get("note") or "") or None
            if disc.get("posts"):
                options = top_competitor_options(tenant_id, artist=artist, k=k, dsn=dsn)
        except Exception as exc:  # noqa: BLE001 — discovery must never wedge the run
            discovery_note = f"live competitor discovery failed: {type(exc).__name__}"
    if not options:
        if discovery_note:
            return "skip", f"{NO_COMPETITOR_NOTE} ({discovery_note})"
        return "skip", NO_COMPETITOR_NOTE
    question = (
        f"I scored {len(options)} competitor post{'s' if len(options) != 1 else ''} "
        "— which pattern should I mold for this run? (Only its shape is reused; "
        "the wording, artwork and offers stay ours.)"
    )
    plan_snapshot = None
    try:
        plan_snapshot = plan.model_dump() if hasattr(plan, "model_dump") else None
    except Exception:
        plan_snapshot = None
    try:
        request_selection(
            run_id, tenant_id, session_id,
            question=question, options=options, plan_snapshot=plan_snapshot, dsn=dsn,
        )
    except Exception:
        # A store failure must not wedge the run behind a pause nobody can
        # answer: proceed on the normal IG path, honestly noted.
        return "skip", (
            "competitor options could not be persisted for selection — "
            "proceeding without competitor intel"
        )
    payload = {"kind": "competitor_pick", "question": question, "options": options}
    if discovery_note:
        payload["note"] = discovery_note  # the honest live-discovery step note
    return "pause", payload


# --------------------------------------------------------------------------- #
# Style-match seam for pause #2 (the EXISTING artwork pause, fed our terms).
# --------------------------------------------------------------------------- #
def competitor_theme_terms(
    pick: dict[str, Any] | None, image_style: str | None = None
) -> list[str]:
    """Artwork-matching terms for the artwork TOP-4: the chosen post's REAL
    ``visualTags`` merged with the plan's ``image_style`` words — so pause #2
    surfaces style-matched pieces from OUR OWN portfolio (matching the pattern's
    look, never the competitor's images). Pure; de-duped, bounded like
    :func:`studio.artwork_flow.theme_terms_from_plan`."""
    terms: list[str] = []
    for t in (pick or {}).get("visualTags") or []:
        if isinstance(t, str) and t.strip():
            terms.append(t.strip())
    for w in str(image_style or "").split():
        if w.strip():
            terms.append(w.strip())
    seen: set[str] = set()
    out: list[str] = []
    for t in terms:
        key = t.lower()
        if key not in seen:
            seen.add(key)
            out.append(t)
        if len(out) >= 16:
            break
    return out


# --------------------------------------------------------------------------- #
# The MOLD step (never copy — hard product/safety rule, enforced in code).
# --------------------------------------------------------------------------- #
def _norm_text(text: str) -> str:
    """Whitespace/case/punctuation-insensitive canonical form for the verbatim
    guard. Pure."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", "", (text or "").lower())).strip()


def copies_verbatim(candidate: str, competitor_caption: str) -> bool:
    """True when ``candidate`` reuses the competitor caption — or any of its
    sentences of 4+ words — verbatim (case/punctuation-insensitive). The
    deterministic check behind the NEVER-copy rule; every molded field passes
    through it. Pure."""
    from studio.competitor_intel import _sentences

    cand = _norm_text(candidate)
    if not cand:
        return False
    cap = _norm_text(competitor_caption)
    if cap and cap in cand:
        return True
    for s in _sentences(competitor_caption):
        ns = _norm_text(s)
        if ns and len(ns.split()) >= 4 and ns in cand:
            return True
    return False


def _guarded(candidate: str, fallback: str, competitor_caption: str) -> str:
    """``candidate`` unless it leaks competitor wording (then the safe shape-only
    ``fallback``) — defense in depth under the never-copy rule. Pure."""
    if candidate.strip() and not copies_verbatim(candidate, competitor_caption):
        return candidate.strip()
    return fallback


def _hook_shape(hook_line: str) -> str:
    """A SHAPE-ONLY description of the reference hook (question / imperative /
    number signals — never its words). Pure, deterministic."""
    from studio.competitor_intel import _IMPERATIVE_VERBS

    bits: list[str] = []
    if "?" in (hook_line or ""):
        bits.append("a question")
    first = re.findall(r"[A-Za-z']+", hook_line or "")
    if first and first[0].lower() in _IMPERATIVE_VERBS:
        bits.append("an imperative")
    if re.search(r"\d", hook_line or ""):
        bits.append("a concrete number")
    if not bits:
        return "a direct opening line"
    return " + ".join(bits)


def mold_competitor_pattern(
    tenant_id: str,
    plan: Any,
    pick: dict[str, Any],
    *,
    run_id: str | None = None,
    campaign_id: str | None = None,
    dsn: str | None = None,
) -> dict[str, Any]:
    """MOLD the operator-picked competitor post into OUR brand — angle, hook and
    structure adapted, NEVER copied:

      * deterministic core (always runs): the post's SHAPE via
        :func:`studio.competitor_intel.deconstruct_caption` (structure part
        labels + emotional angle + hook-shape signals) rewritten as a drafting
        direction grounded in OUR proven brand patterns
        (:func:`studio.ig_pipeline.load_brand_patterns` — real past-campaign
        hooks/CTAs, our own copy);
      * one optional policy-clamped run of the EXISTING brand molding cell
        (:func:`cells.copywriter.build_copywriter_cell`, whose whole job is to
        mold a pattern into the artist's tenant-pack voice) refines the hook/CTA
        when a key is armed — honest skip (``llm_refined`` False, error named)
        otherwise;
      * EVERY produced field passes :func:`copies_verbatim` — a leaked
        competitor sentence falls back to the shape-only direction, so the
        never-copy rule holds in code, not just in prompt text.

    Recorded as ONE ``role='molder'`` agent_run (deterministic id — a resume
    re-records it as a no-op) whose input carries the reference post's
    caption/visual_tags and whose output is the brand-adapted pattern."""
    from studio.competitor_intel import deconstruct_caption
    from studio.ig_pipeline import load_brand_patterns

    caption = str(pick.get("caption") or "")
    visual_tags = [str(t) for t in (pick.get("visualTags") or [])]
    shape = deconstruct_caption(caption, visual_tags)
    artist = (getattr(plan, "artist", "") or "").strip() or None
    goal = (getattr(plan, "goal", "") or "").strip()
    patterns = load_brand_patterns(tenant_id, artist, dsn=dsn)

    parts = [s.get("part") for s in shape.get("structure") or []] or ["hook", "context", "cta"]
    angle = shape.get("emotional_angle") or "showcase"
    hook_shape = _hook_shape(shape.get("hook_line") or "")

    proven_hooks = patterns.get("campaign_hooks") or []
    proven_ctas = patterns.get("campaign_ctas") or []
    proven_hook = str(proven_hooks[0].get("hook") or "") if proven_hooks else ""
    proven_cta = str(proven_ctas[0].get("cta") or "") if proven_ctas else ""

    # Deterministic brand-adapted directions: built ONLY from the abstract shape
    # labels, OUR proven copy, and the plan's own goal — no competitor words.
    base_hook = (
        f"Open with {hook_shape} on the {angle} angle, written fresh in OUR "
        "brand voice"
    )
    hook = base_hook
    if goal:
        hook += f", about: {goal}"
    if proven_hook:
        hook += f' — model it on our proven hook: "{proven_hook}"'
    base_cta = "Close with a CTA in OUR voice pointing at OUR booking path"
    cta = base_cta
    if proven_cta:
        cta += f' — reuse our proven CTA: "{proven_cta}"'

    llm_refined = False
    llm_error: str | None = None
    if os.environ.get("ANTHROPIC_API_KEY") and caption.strip():
        try:
            from cells.copywriter import build_copywriter_cell
            from studio.customer_research import resolve_brand_voice

            voice, claims = resolve_brand_voice(tenant_id)
            cell = build_copywriter_cell(
                brand_voice_context=voice, approved_claims=claims
            )  # default model stays under the 8sk clamp
            got = cell.run_sync(
                "Winning angle (a competitor pattern's ABSTRACT shape — "
                "structure reference ONLY; NEVER reuse, quote, or extend the "
                "competitor's words, offers, or claims): "
                f"emotional angle {angle}; structure {' -> '.join(str(p) for p in parts)}; "
                f"hook shape: {hook_shape}.\n"
                "Platform: instagram\n"
                + (f"Goal: {goal}\n" if goal else "")
                + "Write the hook and CTA fresh in OUR brand voice."
            )
            v = got.variants[0]
            cand_hook = _guarded(v.hook, hook, caption)
            cand_cta = _guarded(v.call_to_action, cta, caption)
            if cand_hook != hook or cand_cta != cta:
                llm_refined = True
            hook, cta = cand_hook, cand_cta
        except Exception as exc:  # honest degradation — deterministic mold stands
            llm_error = type(exc).__name__

    # Final clamp (defense in depth): no molded field carries competitor wording.
    hook = _guarded(hook, base_hook, caption)
    cta = _guarded(cta, base_cta, caption)
    draft_output = (
        f"angle: {angle} | structure: {' -> '.join(str(p) for p in parts)} | "
        f"hook: {hook} | cta: {cta}"
    )

    mold: dict[str, Any] = {
        "reference_post_id": pick.get("postId"),
        "reference_handle": pick.get("handle"),
        "reference_url": pick.get("url"),
        "emotional_angle": angle,
        "structure": [str(p) for p in parts],
        "hook": hook,
        "cta": cta,
        "draft_output": draft_output,
        "visual_pattern": ", ".join(visual_tags) or None,
        "llm_refined": llm_refined,
        "never_copy": (
            "competitor caption used as SHAPE reference only — no sentence "
            "reused verbatim"
        ),
    }
    if llm_error:
        mold["llm_error"] = llm_error

    if run_id:
        # One visible crew step, deterministic id (ON CONFLICT DO NOTHING) so the
        # post-pause resume re-records it as a no-op. Best-effort like every
        # grounding step — a store hiccup never breaks the run.
        from studio.ig_pipeline import _record_crew_step

        _record_crew_step(
            dsn, run_id, campaign_id,
            "molder",
            "db+cell" if llm_refined else "db",
            {
                "reference_post_id": pick.get("postId"),
                "handle": pick.get("handle"),
                "caption": caption,
                "visual_tags": visual_tags,
            },
            mold,
        )
    return mold


def render_molded_block(
    mold: dict[str, Any], pick: dict[str, Any] | None = None
) -> str:
    """The brief block carrying the OPERATOR-PICKED competitor pattern, already
    molded to OUR brand — it ORDERS the drafter: follow the molded direction;
    artwork ONLY from our library; wording in OUR brand voice; offers ONLY
    substantiated codes; NEVER copy competitor sentences verbatim."""
    handle = mold.get("reference_handle")
    lines = [
        "\nCOMPETITOR PATTERN (operator-picked, MOLDED to our brand). The "
        "operator chose one competitor post as the SHAPE reference for this run. "
        "Use ONLY the molded direction below: structure and angle from the "
        "pattern; artwork ONLY from our library; wording in OUR brand voice; "
        "offers ONLY substantiated codes; NEVER copy competitor sentences "
        "verbatim.",
        f"  - reference: @{handle}"
        + (f" — {mold.get('reference_url')}" if mold.get("reference_url")
           else " — (no url provided)"),
        f"  - structure to follow: {' -> '.join(mold.get('structure') or [])}",
        f"  - emotional angle: {mold.get('emotional_angle')}",
        f"  - molded hook direction (ours): {mold.get('hook')}",
        f"  - molded CTA direction (ours): {mold.get('cta')}",
    ]
    if mold.get("visual_pattern"):
        lines.append(
            "  - visual pattern to match from OUR OWN portfolio: "
            f"{mold['visual_pattern']}"
        )
    if pick and pick.get("whyItWorked"):
        lines.append(f"  - why the reference worked: {pick['whyItWorked']}")
    return "\n".join(lines)


def awaiting_competitor_summary(
    run_id: str | None,
    campaign_id: str | None,
    selection_request: dict[str, Any],
    *,
    channel: str,
    agent_runs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The run summary returned when the executor PAUSES for the competitor pick
    (pause #1) — the registry marks the run ``awaiting_selection`` and the poller
    surfaces the request. Nothing was molded, drafted, staged, or sent. A
    ``note`` on the request (the live-discovery honest counts) surfaces as an
    extra step note."""
    n = len(selection_request.get("options") or [])
    step_notes = [
        f"paused before molding/drafting: {n} scored competitor post(s) "
        "surfaced for the operator's choice (POST "
        "/studio/campaign/{run_id}/select-competitor resumes)"
    ]
    if selection_request.get("note"):
        step_notes.insert(0, str(selection_request["note"]))
    return {
        "run_id": run_id,
        "campaign_id": campaign_id,
        "routed_channel": channel,
        "pipeline_built": True,
        "run_status": "awaiting_selection",
        "archetype_id": None,
        "agent_runs": list(agent_runs or []),
        "n_pending": 0,
        "n_queued": 0,
        "channels": [channel],
        "runs_row": False,
        "selection_request": selection_request,
        "message": selection_request.get("question")
        or f"{n} competitor post option(s) await your pick before drafting.",
        "step_notes": step_notes,
        "failure_summary": [],
    }
