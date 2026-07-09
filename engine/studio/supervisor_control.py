"""Supervisor control channel — FULL-DUPLEX steering of a live campaign run.

The supervisor (voice/chat host, or the operator directly) does not just WATCH a
run (that is ``live_state``/the progress board); with this module it can REACH IN:

  * ``issue_directive`` writes a durable ``run_directives`` row (pause / abort /
    set_angle / set_offer / skip_lead / guide_copy);
  * the executor calls :func:`apply_directives` at every safe boundary (before
    each lead is processed), so a directive lands MID-RUN, not after the fact;
  * every application is recorded as a ``role='supervisor'`` agent_run — the
    intervention is visible in the live agent panel with the same lineage as any
    other agent step, never a silent mutation;
  * :func:`review_run_coherence` is the supervisor's contradiction check between
    the agents' REAL recorded outputs (researcher vs strategist vs analyst) —
    deterministic rules first, one policy-clamped LLM read on top when a key is
    configured. It returns an honest verdict + a SUGGESTED directive; it never
    auto-applies (the supervisor/operator decides).

Safety posture: directives can only NARROW or REDIRECT a run (fewer leads, a
different substantiated offer, a different angle, guidance text, stop). They can
never widen delivery, lift HOLD, or bypass the send gates — there is deliberately
no directive kind for any of those.
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any

VALID_KINDS = (
    "pause", "abort", "set_angle", "set_offer", "skip_lead", "guide_copy", "redo_lead",
)

_DDL = """
CREATE TABLE IF NOT EXISTS run_directives (
    id         TEXT PRIMARY KEY,
    run_id     TEXT NOT NULL,
    tenant_id  TEXT NOT NULL,
    kind       TEXT NOT NULL,
    payload    JSONB NOT NULL DEFAULT '{}'::jsonb,
    issued_by  TEXT NOT NULL DEFAULT 'operator',
    status     TEXT NOT NULL DEFAULT 'pending',
    note       TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    applied_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS run_directives_run_idx ON run_directives (run_id, status);
"""


def _dsn(dsn: str | None) -> str:
    return dsn or os.environ.get(
        "ENGINE_DATABASE_URL", "postgresql://scalers:scalers@localhost:5432/scalers"
    )


def _connect(dsn: str | None):
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(_dsn(dsn), row_factory=dict_row, autocommit=True)


def ensure_schema(dsn: str | None = None) -> None:
    with _connect(dsn) as conn:
        conn.execute(_DDL)


def issue_directive(
    run_id: str,
    tenant_id: str,
    kind: str,
    payload: dict[str, Any] | None = None,
    *,
    issued_by: str = "operator",
    dsn: str | None = None,
) -> dict[str, Any]:
    """Durably queue one steering directive for a run. Raises on an unknown kind —
    the closed set is the safety boundary (no directive can widen delivery)."""
    if kind not in VALID_KINDS:
        raise ValueError(f"unknown directive kind {kind!r}; allowed: {', '.join(VALID_KINDS)}")
    ensure_schema(dsn)
    row_id = "dir_" + uuid.uuid4().hex[:16]
    with _connect(dsn) as conn:
        conn.execute(
            "INSERT INTO run_directives (id, run_id, tenant_id, kind, payload, issued_by) "
            "VALUES (%s, %s, %s, %s, %s::jsonb, %s)",
            (row_id, run_id, tenant_id, kind, json.dumps(payload or {}), issued_by),
        )
    return {"id": row_id, "run_id": run_id, "kind": kind, "payload": payload or {}, "status": "pending"}


def list_directives(run_id: str, *, dsn: str | None = None) -> list[dict[str, Any]]:
    ensure_schema(dsn)
    with _connect(dsn) as conn:
        rows = conn.execute(
            "SELECT id, kind, payload, issued_by, status, note, created_at, applied_at "
            "FROM run_directives WHERE run_id=%s ORDER BY created_at",
            (run_id,),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        for k in ("created_at", "applied_at"):
            if d.get(k) is not None:
                d[k] = d[k].isoformat()
        out.append(d)
    return out


def apply_directives(
    run_id: str,
    tenant_id: str,
    *,
    dsn: str | None = None,
    record_agent_run=None,
) -> dict[str, Any]:
    """Consume every PENDING directive for ``run_id`` and return the effective
    steering for the executor to honor at this boundary:

        {"abort": bool, "pause": bool, "angle": str|None, "offer_code": str|None,
         "skip_customer_ids": set[str], "guidance": [str, ...], "applied": [rows]}

    Each consumed directive is marked applied and (when ``record_agent_run`` is
    given) recorded as a ``role='supervisor'`` agent_run so the live panel shows
    the intervention in-line with the other agents' steps."""
    ensure_schema(dsn)
    out: dict[str, Any] = {
        "abort": False, "pause": False, "angle": None, "offer_code": None,
        "skip_customer_ids": set(), "redo_customer_ids": set(), "guidance": [],
        "applied": [],
    }
    with _connect(dsn) as conn:
        rows = conn.execute(
            "SELECT id, kind, payload, issued_by FROM run_directives "
            "WHERE run_id=%s AND status='pending' ORDER BY created_at",
            (run_id,),
        ).fetchall()
        for r in rows:
            kind = r["kind"]
            payload = r["payload"] or {}
            note = ""
            if kind == "abort":
                out["abort"] = True
                note = "run aborted at the next safe boundary"
            elif kind == "pause":
                out["pause"] = True
                note = "run paused at the next safe boundary (re-run resumes; staged leads replay-skip)"
            elif kind == "set_angle":
                angle = str(payload.get("angle") or "").strip()
                if angle:
                    out["angle"] = angle
                    note = f"campaign angle redirected: {angle[:120]}"
                else:
                    note = "set_angle ignored: empty angle"
            elif kind == "set_offer":
                code = str(payload.get("code") or "").strip()
                if code:
                    out["offer_code"] = code
                    note = f"offer switched to code {code} (substantiation still gates it)"
                else:
                    note = "set_offer ignored: empty code"
            elif kind == "skip_lead":
                cid = str(payload.get("customer_id") or "").strip()
                if cid:
                    out["skip_customer_ids"].add(cid)
                    note = f"lead {cid} will be skipped"
                else:
                    note = "skip_lead ignored: no customer_id"
            elif kind == "guide_copy":
                text = str(payload.get("text") or "").strip()
                if text:
                    out["guidance"].append(text)
                    note = "copy guidance injected into subsequent drafts"
                else:
                    note = "guide_copy ignored: empty text"
            elif kind == "redo_lead":
                cid = str(payload.get("customer_id") or "").strip()
                if cid:
                    out["redo_customer_ids"].add(cid)
                    note = (
                        f"lead {cid} queued for re-processing (only applies if it has "
                        "not already staged in this run)"
                    )
                else:
                    note = "redo_lead ignored: no customer_id"
            conn.execute(
                "UPDATE run_directives SET status='applied', note=%s, applied_at=now() WHERE id=%s",
                (note, r["id"]),
            )
            applied = {"id": r["id"], "kind": kind, "payload": payload, "note": note}
            out["applied"].append(applied)
            if record_agent_run is not None:
                try:
                    record_agent_run(
                        role="supervisor",
                        model=f"directive:{r['issued_by']}",
                        input={"directive": kind, "payload": payload},
                        output={"applied": True, "note": note},
                    )
                except Exception:
                    pass  # visibility is best-effort; the steering itself already applied
    return out


# --------------------------------------------------------------------------- #
# Coherence review — the supervisor's contradiction check between agents.
# --------------------------------------------------------------------------- #
def _latest_outputs(run_id: str, dsn: str | None) -> dict[str, list[dict[str, Any]]]:
    with _connect(dsn) as conn:
        rows = conn.execute(
            "SELECT role, output, created_at FROM agent_runs WHERE run_id=%s ORDER BY created_at",
            (run_id,),
        ).fetchall()
    by_role: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_role.setdefault(r["role"], []).append(r["output"] or {})
    return by_role


def review_run_coherence(
    run_id: str,
    tenant_id: str,
    *,
    dsn: str | None = None,
) -> dict[str, Any]:
    """The supervisor's audit of a run's internal coherence, from the agents' REAL
    recorded outputs. Deterministic rules first; one policy-clamped LLM read on top
    when configured. Returns an honest verdict + suggested directive(s) — it never
    applies anything itself."""
    outputs = _latest_outputs(run_id, dsn)
    findings: list[dict[str, str]] = []

    strategist = (outputs.get("strategist") or [{}])[-1]
    angle = str(
        (strategist.get("strategy") or {}).get("target_angle")
        if isinstance(strategist.get("strategy"), dict)
        else strategist.get("target_angle") or ""
    )

    # Rule 1 — the strategist's angle rests on research, but the researcher found nothing.
    research_rows = outputs.get("researcher") or []
    research_empty = bool(research_rows) and all(
        not (o.get("sources") or o.get("findings") or o.get("hits")) for o in research_rows
    )
    if research_empty and any(w in angle.lower() for w in ("research", "trend", "data shows")):
        findings.append({
            "rule": "angle-cites-empty-research",
            "detail": "strategist angle leans on research/trends but every researcher step returned no sources",
            "suggest": "set_angle to a grounded angle that does not cite research",
        })

    # Rule 2 — the angle names a discount that no substantiated offer backs.
    try:
        from studio.offers import get_offers

        codes = {(o.code or "").lower() for o in get_offers(tenant_id, dsn=dsn)}
        import re as _re

        for tok in _re.findall(r"\$\d+|\d+%\s*off|code\s+[A-Z0-9]+", angle, _re.IGNORECASE):
            if not any(c and c in angle.lower() for c in codes):
                findings.append({
                    "rule": "angle-unsubstantiated-offer",
                    "detail": f"angle mentions {tok!r} but no substantiated offer code appears in it",
                    "suggest": "set_offer with a real offer code, or set_angle without discount language",
                })
                break
    except Exception:
        pass

    # Rule 3 — analyst-measured dominant objection contradicts the angle's premise.
    analyst_rows = outputs.get("analyst") or []
    objections = [str(o.get("primary_objection") or o.get("objection") or "") for o in analyst_rows]
    objections = [o for o in objections if o and o != "none-found"]
    if objections and angle:
        from collections import Counter

        dominant, n = Counter(objections).most_common(1)[0]
        if n >= 2 and dominant.lower() not in angle.lower():
            findings.append({
                "rule": "angle-ignores-dominant-objection",
                "detail": f"analysts measured {dominant!r} as the dominant objection ({n} leads) but the angle does not address it",
                "suggest": f"set_angle to one that answers the {dominant!r} objection",
            })

    verdict = {
        "run_id": run_id,
        "contradiction": bool(findings),
        "findings": findings,
        "checked_roles": sorted(outputs.keys()),
        "llm_read": False,
    }

    # Optional LLM read (policy-clamped) — only when a key is configured; honest skip otherwise.
    if os.environ.get("ANTHROPIC_API_KEY") and outputs.get("strategist"):
        try:
            from pydantic import BaseModel

            from cells.base import Cell

            class _Verdict(BaseModel):
                contradiction: bool
                why: str

            researcher_txt = json.dumps((outputs.get("researcher") or [{}])[-1], default=str)[:1500]
            analyst_txt = json.dumps((outputs.get("analyst") or [{}])[-1], default=str)[:1200]
            prompt = (
                f"STRATEGIST ANGLE: {angle[:600]}\n"
                f"LATEST RESEARCHER OUTPUT: {researcher_txt}\n"
                f"LATEST ANALYST OUTPUT: {analyst_txt}\n"
                "Contradiction = the angle asserts something these outputs undermine or "
                "cannot support. Respond with contradiction true/false and a one-sentence why."
            )
            cell = Cell(
                name="supervisor_coherence",
                schema=_Verdict,
                instructions=(
                    "You are the campaign supervisor auditing two agents' REAL recorded "
                    "outputs for internal contradiction. Judge ONLY from the provided "
                    "text; never invent facts."
                ),
            )  # default model under the 8sk clamp
            got = cell.run_sync(prompt)
            verdict["llm_read"] = True
            if got.contradiction:
                findings.append({"rule": "llm-coherence", "detail": got.why[:300], "suggest": "review and steer via set_angle/guide_copy"})
                verdict["contradiction"] = True
                verdict["findings"] = findings
        except Exception as exc:  # honest degradation — deterministic rules already ran
            verdict["llm_read"] = False
            verdict["llm_error"] = f"{type(exc).__name__}"
    return verdict


# --------------------------------------------------------------------------- #
# Plan conformance — did the agents actually DO what the plan ordered?
# --------------------------------------------------------------------------- #
def check_plan_conformance(
    plan: Any,
    agent_runs: list[dict[str, Any]],
    *,
    fired_rules: set[str] | None = None,
) -> list[dict[str, str]]:
    """PURE: compare the plan's explicit orders against the agents' REAL recorded
    steps so far; return NEW findings (rules not already in ``fired_rules``), each
    with a concrete corrective suggestion. The executor records every finding as a
    role='supervisor' step and auto-injects the correction — the operator's order
    ("keep steering until it matches the plan") made this enforcement, not advice.

    Rules (deterministic, evidence-only):
      * plan asked for DEEP RESEARCH  -> every researcher step must carry sources;
        an empty researcher step is a miss.
      * plan asked to PERSONALIZE     -> an analyst (psych) step must exist for
        every drafted lead; a draft without a preceding analyst step is a miss.
      * plan asked to ATTACH ARTWORK  -> an artwork selection step must exist
        before drafting begins.
    """
    fired = fired_rules if fired_rules is not None else set()
    findings: list[dict[str, str]] = []

    deep = bool(getattr(plan, "deep_research", None)) or (
        str(getattr(plan, "research_depth", "") or "").strip().lower() == "deep"
    )
    if deep and "research-missing" not in fired:
        researcher = [ar for ar in agent_runs if ar.get("role") == "researcher"]
        empty = [
            ar for ar in researcher
            if not ((ar.get("output") or {}).get("sources") or (ar.get("output") or {}).get("hits"))
        ]
        if researcher and len(empty) == len(researcher):
            findings.append({
                "rule": "research-missing",
                "detail": (
                    "the plan orders deep research but every researcher step so far "
                    "returned no sources"
                ),
                "correction": (
                    "Ground every claim in on-file facts only; do NOT imply research "
                    "was done. Flag leads needing research for a follow-up pass."
                ),
            })

    personalize = getattr(plan, "personalize", None) is True or getattr(plan, "per_lead", None) is True
    if personalize and "analysis-missing" not in fired:
        drafted = [ar for ar in agent_runs if ar.get("role") == "draft"]
        analysts = [ar for ar in agent_runs if ar.get("role") == "analyst"]
        drafted_ids = {(ar.get("input") or {}).get("customer_id") for ar in drafted}
        analyzed_ids = {(ar.get("input") or {}).get("customer_id") for ar in analysts}
        missing = {c for c in drafted_ids if c} - {c for c in analyzed_ids if c}
        if missing:
            findings.append({
                "rule": "analysis-missing",
                "detail": (
                    f"the plan orders per-lead psychometric analysis but {len(missing)} "
                    f"drafted lead(s) have no analyst step: {sorted(missing)[:3]}"
                ),
                "correction": (
                    "Run the psych analysis for every remaining lead BEFORE drafting; "
                    "the listed leads should be re-processed (redo_lead) after their "
                    "pending drafts are rejected."
                ),
            })

    attach = getattr(plan, "attach_artwork", None) is True
    if attach and "artwork-missing" not in fired:
        drafted = [ar for ar in agent_runs if ar.get("role") == "draft"]
        has_artwork_step = any(
            "artwork" in json.dumps(ar.get("output") or {}, default=str).lower()
            for ar in agent_runs
        )
        if drafted and not has_artwork_step:
            findings.append({
                "rule": "artwork-missing",
                "detail": "the plan orders artwork attachment but no artwork selection step is recorded",
                "correction": "Pause and run the artwork top-4 selection before further drafting.",
            })

    for f in findings:
        fired.add(f["rule"])
    return findings
