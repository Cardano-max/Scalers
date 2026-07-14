"""Console read API for ju1.5 (SD-FRONTEND) — campaign-example memory + draft lineage.

Three additive, read-only endpoints the Next.js console binds:

* ``GET /studio/campaign-examples``            — the tenant's real campaign example
  library (ju1.2 store) + extracted patterns, for the Campaign Memory view.
* ``GET /studio/campaign-examples/{id}/screenshot`` — streams the example's source
  screenshot from the LOCAL client-data directory. The filename comes ONLY from the
  example's own DB row (never from the request), so there is no path traversal and
  nothing is ever uploaded anywhere.
* ``GET /studio/action/{id}/lineage``          — the review-queue draft lineage:
  source CSV / customer name-email-phone / artist / studio / offer / CTA / channel /
  campaign examples referenced. Fields the system cannot ground are HONEST ``None``
  (the console renders "missing", never a blank fake). ``examples`` is ``[]`` until
  ju1.4 wires per-draft example provenance into the generator.

Kept in its own module (not ``studio/agui.py``) so the hot AG-UI module stays
collision-free for in-flight beads; mounted from ``engine/main.py``.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import FileResponse

router = APIRouter()

# Where the operator's local client-data lives (screenshots referenced by the
# campaign_examples rows). Same default the ju1.1 importer CLI uses
# (studio/client_import.py); env-overridable for other machines/CI.
_CLIENT_DATA_DIR = "SCALERS_CLIENT_DATA_DIR"
_DEFAULT_CLIENT_DATA = "C:/Users/Links/Desktop/CustomerAcq/client-data"


def _client_data_dir() -> Path:
    return Path(os.environ.get(_CLIENT_DATA_DIR) or _DEFAULT_CLIENT_DATA)


def _jsonable(value: Any) -> Any:
    """Coerce DB row values (Decimal / datetime) to JSON-safe primitives."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


@router.get("/studio/campaign-examples")
def campaign_examples(tenant_id: str) -> dict:
    """The tenant's campaign-example memory: real examples + extracted patterns.

    Honest-empty for a tenant with none (never a fabricated example). Each example
    gains ``screenshot_url`` when its screenshot file is actually present locally —
    the console renders the image through the sibling endpoint below.
    """
    from studio.campaign_examples_store import get_examples, get_patterns

    examples = []
    for row in get_examples(tenant_id):
        ex = {k: _jsonable(v) for k, v in row.items()}
        shot = _resolve_screenshot(row.get("source_screenshot"))
        ex["screenshot_url"] = (
            f"/studio/campaign-examples/{row['id']}/screenshot" if shot else None
        )
        examples.append(ex)
    patterns = [{k: _jsonable(v) for k, v in row.items()} for row in get_patterns(tenant_id)]
    return {"tenantId": tenant_id, "examples": examples, "patterns": patterns}


def _resolve_screenshot(source_screenshot: str | None) -> Path | None:
    """Locate the example's screenshot under the local client-data dir.

    ``source_screenshot`` is the stored Slack file id (with or without extension).
    Only that DB value is used — never request input — and the result must stay
    inside the screenshots dir. Missing file -> None (honest-missing)."""
    if not source_screenshot:
        return None
    base = _client_data_dir() / "screenshots"
    name = Path(source_screenshot).name  # strips any stray path components
    candidates = [name] if "." in name else [f"{name}{ext}" for ext in (".png", ".jpg", ".jpeg")]
    for cand in candidates:
        p = base / cand
        if p.is_file():
            return p
    return None


@router.get("/studio/campaign-examples/{example_id}/screenshot")
def campaign_example_screenshot(example_id: str) -> FileResponse:
    """Stream the example's source screenshot from the LOCAL client-data dir.

    The filename is read from the example's own row; a request can only ever name
    an example id. 404 when the example or its file is absent."""
    from studio.campaign_examples_store import _connect  # same store, same DSN chain

    try:
        with _connect(None) as conn:
            row = conn.execute(
                "SELECT source_screenshot FROM campaign_examples WHERE id = %s",
                (example_id,),
            ).fetchone()
    except Exception:
        row = None
    if row is None:
        raise HTTPException(status_code=404, detail=f"unknown campaign example {example_id!r}")
    path = _resolve_screenshot(row["source_screenshot"])
    if path is None:
        raise HTTPException(status_code=404, detail="screenshot file not present locally")
    media = "image/jpeg" if path.suffix.lower() in (".jpg", ".jpeg") else "image/png"
    return FileResponse(path, media_type=media)


def _dossier_field(dossier: dict, key: str) -> tuple[Any, str | None]:
    """A DossierField's ``(value, source)`` — honest ``(None, None)`` when absent."""
    f = dossier.get(key)
    if isinstance(f, dict):
        return f.get("value"), f.get("source")
    return None, None


@router.get("/studio/action/{action_id}/lineage")
def action_lineage(action_id: str) -> dict:
    """Draft lineage for the review queue (ju1.5): where this draft CAME from.

    Assembled from the staged action's dossier context + the customers/artists
    tables. Every field the system cannot ground is ``None`` — the console renders
    an explicit "missing", never a fabricated value.
    """
    from actions.store import get_action

    action = get_action(action_id)
    if action is None:
        raise HTTPException(status_code=404, detail=f"unknown action {action_id!r}")

    dossier: dict[str, Any] = {}
    if action.context:
        try:
            ctx = json.loads(action.context)
            if isinstance(ctx, dict) and isinstance(ctx.get("dossier"), dict):
                dossier = ctx["dossier"]
        except (ValueError, TypeError):
            dossier = {}

    name, _ = _dossier_field(dossier, "name")
    email, _ = _dossier_field(dossier, "email")
    phone, _ = _dossier_field(dossier, "phone")
    cta, _ = _dossier_field(dossier, "recommended_cta")
    _, angle_source = _dossier_field(dossier, "best_angle")
    # The dossier records a substantiated offer only as a "+offer:CODE" suffix on
    # the angle's source (dossier.py) — surface the code; None when no real offer.
    offer = None
    if angle_source and "+offer:" in angle_source:
        offer = angle_source.rsplit("+offer:", 1)[1] or None

    customer_id = dossier.get("customer_id")
    source_file, artist, studio = _customer_lineage(action.tenant_id, customer_id, email)

    # Per-draft example provenance (ju1.7 wiring of the ju1.4 generator): a draft staged
    # by the example-grounded campaign_generator carries the REAL campaign-example ids it
    # was built on in ``context.grounded_example_ids``. Resolve them to their real names so
    # the lineage panel shows exactly which past campaigns grounded this draft. Honest-empty
    # for any other draft (per-lead outreach has no campaign-example provenance).
    ctx_all: dict[str, Any] = {}
    if action.context:
        try:
            _c = json.loads(action.context)
            ctx_all = _c if isinstance(_c, dict) else {}
        except (ValueError, TypeError):
            ctx_all = {}
    examples = _resolve_example_lineage(action.tenant_id, ctx_all.get("grounded_example_ids"))
    # The campaign_generator records the operator's offer on the action context too.
    if offer is None and ctx_all.get("offer_price_usd"):
        offer = f"${int(ctx_all['offer_price_usd']):,}"
    if not artist and ctx_all.get("artist"):
        artist = ctx_all["artist"]

    return {
        "actionId": action.id,
        "runId": action.run_id,
        "channel": action.channel,
        "sourceFile": source_file,
        "customer": {"id": customer_id, "name": name, "email": email, "phone": phone},
        "artist": artist,
        "studio": studio,
        "offer": offer,
        "cta": cta,
        # Real per-draft campaign-example provenance (ju1.7). Empty for non-generator drafts.
        "examples": examples,
        "limitedPersonalization": dossier.get("limited_personalization"),
        "personalizationNote": dossier.get("personalization_note"),
    }


def _resolve_example_lineage(
    tenant_id: str, example_ids: Any
) -> list[dict[str, Any]]:
    """Resolve grounded campaign-example ids to their REAL names for the lineage panel.

    ``example_ids`` is the ``context.grounded_example_ids`` list a campaign_generator draft
    carries (ju1.4). Returns ``[{id, campaignName, artist}]`` in the given order, dropping
    any id no longer on file. Honest-empty ``[]`` when there are none or on any store
    hiccup — never a fabricated example."""
    if not isinstance(example_ids, (list, tuple)) or not example_ids:
        return []
    ids = [str(i) for i in example_ids if str(i or "").strip()]
    if not ids:
        return []
    try:
        from studio.campaign_examples_store import _connect

        with _connect(None) as conn:
            rows = conn.execute(
                "SELECT id, campaign_name, artist_name FROM campaign_examples "
                "WHERE tenant_id = %s AND id = ANY(%s)",
                (tenant_id, ids),
            ).fetchall()
        by_id = {r["id"]: r for r in rows}
        out: list[dict[str, Any]] = []
        for i in ids:  # preserve the draft's grounding order
            r = by_id.get(i)
            if r is not None:
                # Shape matches the ju1.5 LineagePanel contract (id + campaign_name).
                out.append({
                    "id": r["id"], "campaign_name": r["campaign_name"],
                    "artist": r["artist_name"],
                })
        return out
    except Exception:
        return []


def _customer_lineage(
    tenant_id: str, customer_id: str | None, email: str | None
) -> tuple[str | None, str | None, str | None]:
    """(source_file, artist, studio) for the draft's customer — honest Nones.

    ``source_file``/``artist``/``shop`` live on the customers row (ju1.1 ext
    columns); when the customer names an artist but no shop, the artist_studios
    mapping (13-artists.sql) supplies the studio. Any store hiccup -> all None."""
    if not customer_id and not email:
        return None, None, None
    try:
        from studio.campaign_examples_store import _connect

        with _connect(None) as conn:
            clauses, params = ["tenant_id = %s"], [tenant_id]
            if customer_id:
                clauses.append("id = %s")
                params.append(customer_id)
            else:
                clauses.append("lower(email) = lower(%s)")
                params.append(email)
            cust = conn.execute(
                "SELECT source_file, artist, shop FROM customers WHERE "
                + " AND ".join(clauses) + " LIMIT 1",
                params,
            ).fetchone()
            if cust is None:
                return None, None, None
            source_file = cust.get("source_file")
            artist = cust.get("artist") or None
            studio = cust.get("shop") or None
            if artist and not studio:
                srow = conn.execute(
                    "SELECT s.studio_name FROM artists a "
                    "JOIN artist_studios s ON s.artist_id = a.id "
                    "WHERE a.tenant_id = %s AND lower(a.name) = lower(%s) LIMIT 1",
                    (tenant_id, artist),
                ).fetchone()
                studio = srow["studio_name"] if srow else None
            return source_file, artist, studio
    except Exception:
        return None, None, None


# ── nmh.6: customer dossier + supervisor memory-state (read-only) ────────────── #


@router.get("/studio/action/{action_id}/contributions")
def action_contributions(action_id: str) -> dict:
    """AGENT CONTRIBUTIONS — why this draft took a team, not one prompt.

    Assembles, from the REAL ``agent_runs`` trail of the draft's run, what each
    agent contributed to THIS draft: purpose, the concrete output it produced,
    the evidence it used, and how the next agent consumed it. Per-lead cells are
    matched on the draft's customer; run-level cells (strategist/jury) apply to
    the whole campaign. Location and identity land as their own entries. Every
    field the system cannot ground is honest-missing — never a fabricated step."""
    import psycopg
    from psycopg.rows import dict_row

    from actions.store import _dsn, get_action

    action = get_action(action_id)
    if action is None:
        raise HTTPException(status_code=404, detail=f"unknown action {action_id!r}")
    ctx: dict[str, Any] = {}
    if action.context:
        try:
            _c = json.loads(action.context)
            ctx = _c if isinstance(_c, dict) else {}
        except (ValueError, TypeError):
            ctx = {}
    dossier = ctx.get("dossier") if isinstance(ctx.get("dossier"), dict) else {}
    customer_id = dossier.get("customer_id")

    rows: list[dict[str, Any]] = []
    if action.run_id:
        with psycopg.connect(_dsn(), autocommit=True, row_factory=dict_row) as conn:
            rows = conn.execute(
                "SELECT role, model, input, output, created_at FROM agent_runs "
                "WHERE run_id = %s ORDER BY created_at",
                (action.run_id,),
            ).fetchall()

    def _mine(role: str) -> dict[str, Any] | None:
        """This customer's cell for a per-lead role — never another lead's cell.
        A customer-less action (a social POST) has no per-lead cells at all, so
        there it falls back to the run-level cell of the same role."""
        for r in rows:
            if r["role"] != role:
                continue
            inp = r.get("input") or {}
            out = r.get("output") or {}
            if customer_id and (inp.get("customer_id") == customer_id
                                or out.get("customer_id") == customer_id):
                return r
        if not customer_id:
            return _run_level(role)
        return None

    def _run_level(role: str) -> dict[str, Any] | None:
        for r in rows:
            if r["role"] == role:
                return r
        return None

    entries: list[dict[str, Any]] = []

    strat = _run_level("strategist")
    if strat:
        out = strat.get("output") or {}
        entries.append({
            "agent": "Strategy",
            "model": strat.get("model"),
            "purpose": "Set the campaign-wide positioning and angle every draft follows.",
            "output": out.get("positioning") or out.get("angle") or "",
            "nextUse": "The copywriter writes inside this strategy — tone, offer "
                       "framing, and CTA logic all come from here.",
            "status": "done",
        })

    res = _mine("researcher")
    if res:
        out = res.get("output") or {}
        enr = out.get("public_enrichment") or {}
        # TWO guardian gates feed one entry: the dossier enrichment pass
        # (public_enrichment.identity) and the raw name-search hits gate
        # (sources_identity.counts). Sum them so the operator sees every
        # candidate the guardian judged for this lead.
        src_gate = out.get("sources_identity") or {}
        idc = dict(enr.get("identity") or {})
        for k, v in (src_gate.get("counts") or {}).items():
            idc[k] = int(idc.get(k) or 0) + int(v or 0)
        db = out.get("db_history") or {}
        cited = int(out.get("cited") or 0)
        evidence = [s.get("url") for s in (out.get("sources") or []) if s.get("url")]
        entries.append({
            "agent": "Research",
            "model": res.get("model"),
            "purpose": "Ground this lead in real first-party history and, when "
                       "authorized, cited public evidence.",
            "output": (f"{cited} cited source(s)" if cited else
                       "no public citations — grounded on first-party data only "
                       "(nothing invented)"),
            "evidence": evidence,
            "dbHistory": {k: v for k, v in db.items() if v not in (None, [], 0)},
            "nextUse": "The analyst classifies objection/readiness from exactly "
                       "this grounding.",
            "status": "degraded" if out.get("degraded") else "done",
        })
        if enr or src_gate:
            unv = [
                f"set aside: {u.get('url')} — {u.get('reason')}"
                for u in [*(enr.get("unverified_detail") or []),
                          *(src_gate.get("set_aside") or [])]
                if u.get("url")
            ][:5]
            entries.append({
                "agent": "Identity Guardian",
                "model": "deterministic:identity-evidence",
                "purpose": "Verify any public profile really is THIS customer — "
                           "never personalize from a stranger with the same name.",
                "output": ((f"{idc.get('confirmed', 0)} confirmed · "
                            f"{idc.get('likely', 0)} likely · "
                            f"{idc.get('uncertain', 0)} uncertain (shown, not used) · "
                            f"{idc.get('rejected', 0)} rejected")
                           if any(idc.values()) else
                           "no public candidates found — nothing to vet; the draft "
                           "stayed on first-party data"),
                **({"evidence": unv} if unv else {}),
                "nextUse": "Only confirmed/likely facts reached the dossier the "
                           "copywriter saw.",
                "status": "done" if any(idc.values()) else "idle",
            })

    # Location — resolved live from the customer row (source + confidence).
    if customer_id:
        try:
            from studio.customer_research import lookup_lead
            from studio.location import resolve_customer_location

            facts = lookup_lead(action.tenant_id, customer_id=customer_id) or {}
            loc = resolve_customer_location(facts)
            entries.append({
                "agent": "Location Resolver",
                "model": "deterministic:on-file-first",
                "purpose": "Target by the CUSTOMER's location, never assume the "
                           "studio's.",
                "output": (f"{loc['display']} (source: {loc['source']}, "
                           f"{'confident' if loc['confident'] else 'not confident'})"
                           if loc.get("display") else
                           "location unknown — not invented; no location-based "
                           "angle used"),
                "nextUse": "Strategy/copy may reference the location only when it "
                           "is grounded.",
                "status": "done" if loc.get("display") else "missing",
            })
        except Exception:
            pass

    ana = _mine("analyst")
    if ana:
        out = ana.get("output") or {}
        grounded = int(out.get("grounded_fields") or 0)
        level = "high" if grounded >= 7 else "medium" if grounded >= 4 else "low"
        entries.append({
            "agent": "Analyst",
            "model": ana.get("model"),
            "purpose": "Classify the REAL objection and readiness from the "
                       "conversation evidence.",
            "output": (f"objection: {out.get('primary_objection')} "
                       f"({out.get('objection_signal')}) · readiness: "
                       f"{out.get('readiness_stage') or '—'}"),
            "personalization": {
                "level": level,
                "reason": f"{grounded} grounded field(s) available for this lead",
            },
            "evidence": out.get("objection_evidence") or out.get("evidence") or [],
            "nextUse": "The copywriter leads with this objection — not a generic "
                       "reactivation line.",
            "status": "done",
        })

    dr = _mine("draft")
    if dr:
        out = dr.get("output") or {}
        if out.get("hook") or out.get("angle"):
            wrote = f"hook: {out.get('hook') or '—'} · angle: {out.get('angle') or '—'}"
        else:  # a social post cell records note/caption instead of hook/angle
            wrote = (out.get("note") or (out.get("caption") or "")[:140]
                     or "draft recorded")
        entries.append({
            "agent": "Copywriter",
            "model": dr.get("model"),
            "purpose": ("Write THIS lead's message in the studio's brand voice."
                        if customer_id else
                        "Write the post caption in the studio's brand voice."),
            "output": wrote,
            "nextUse": "The critic re-verifies evidence and tone before staging.",
            "status": "done",
        })

    cr = _mine("critic")
    if cr:
        out = cr.get("output") or {}
        entries.append({
            "agent": "Critic",
            "model": cr.get("model"),
            "purpose": "Adversarially check the draft: evidence, tone, claims, "
                       "consent language.",
            "output": (f"verdict: {out.get('verdict') or '—'}"
                       + (f" · confidence {out.get('confidence')}" if out.get("confidence") else "")),
            "rationale": out.get("rationale"),
            "nextUse": "Only an approved draft is staged for YOUR review.",
            # An errored critic cell must read as failed — not as a completed check.
            "status": "failed" if out.get("verdict") == "error" else "done",
        })

    jury = _run_level("jury")
    if jury:
        out = jury.get("output") or {}
        entries.append({
            "agent": "Jury",
            "model": jury.get("model"),
            "purpose": "Final send-readiness gate across the whole campaign.",
            "output": (out.get("note") or out.get("finding") or out.get("verdict")
                       or json.dumps(out)[:160]),
            "nextUse": "Drafts stay HELD until you approve — the jury never sends.",
            "status": "done",
        })

    return {
        "actionId": action.id,
        "runId": action.run_id,
        "customerId": customer_id,
        "contributions": entries,
        "agentRunCount": len(rows),
        "note": ("Built from the run's real agent_runs trail — every entry is a "
                 "recorded step, none is narrated after the fact."),
    }


@router.get("/studio/customer/{customer_id}/dossier")
def customer_dossier(customer_id: str, tenant_id: str) -> dict:
    """The on-demand customer dossier (spec §8): real fields + explicit MISSING where
    data is absent, graded personalization_level, never a fabricated depth. 404 when
    the customer does not exist. Built from durable DB state, so it is stable across
    an engine restart."""
    from studio.dossier import build_customer_dossier

    dossier = build_customer_dossier(tenant_id, customer_id)
    if dossier is None:
        raise HTTPException(
            status_code=404,
            detail=f"no customer {customer_id!r} for tenant {tenant_id!r}",
        )
    return dossier.model_dump()


@router.get("/studio/memory-state")
def studio_memory_state(tenant_id: str, artist: str | None = None) -> dict:
    """The supervisor's real-state answer bundle (spec §17): customer count, artists,
    Review-Queue draft counts, failures, and — when ``artist`` is given — that artist's
    stored campaigns + a "last time we ran ..." summary. Pure reads; honest zeroes when
    nothing is stored, never a guess."""
    from studio.supervisor_memory import memory_state

    return memory_state(tenant_id, artist=artist)


def mount_console_api(app: FastAPI) -> None:
    """Attach the console read endpoints (ju1.5 lineage + nmh.6 dossier/memory-state),
    plus the library read API (artifacts + artists — engine-core item 2)."""
    app.include_router(router)
    from studio.library_api import mount_library_api

    mount_library_api(app)
