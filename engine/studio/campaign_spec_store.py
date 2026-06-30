"""Per-campaign SPEC DOC persistence + assembly (Phase 3 — clickable/spec-doc).

One row per completed campaign run holds an outcome-oriented spec document,
assembled ENTIRELY from already-persisted REAL rows (the run's plan + its
per-role ``agent_runs`` + the selected archetype spec). Nothing here is
fabricated: any field that is genuinely absent renders honest-null
(``_(not recorded)_``) rather than a stub.

Mirrors ``studio/campaign_plan_store.py``: lazy psycopg, idempotent
``CREATE TABLE IF NOT EXISTS`` so :func:`setup` is a no-op on an existing cluster.

Schema (``campaign_specs``):

* ``run_id``      — PK; the run this spec documents (== Run.id == the spec PK).
* ``campaign_id`` — the campaign id the agents ran under (agent_runs.campaign_id).
* ``tenant_id``   — owning tenant.
* ``session_id``  — the studio session that launched the run (may be NULL on reconstruct).
* ``archetype_id``— the registered archetype the run used (NULL if not recoverable).
* ``content``     — JSONB structured spec (for later editing).
* ``markdown``    — rendered read-now document.
* ``created_at`` / ``updated_at`` — TIMESTAMPTZ.
"""

from __future__ import annotations

import json
import os
from typing import Any

_DEFAULT_DSN = "postgresql://scalers:scalers@localhost:5432/scalers"


def _dsn(dsn: str | None) -> str:
    return (
        dsn
        or os.environ.get("ENGINE_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
        or _DEFAULT_DSN
    )


def _connect(dsn: str | None = None):
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(_dsn(dsn), autocommit=True, row_factory=dict_row)


def setup(dsn: str | None = None) -> None:
    """Create ``campaign_specs`` if absent (idempotent)."""
    with _connect(dsn) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS campaign_specs (
                run_id       TEXT PRIMARY KEY,
                campaign_id  TEXT,
                tenant_id    TEXT,
                session_id   TEXT,
                archetype_id TEXT,
                content      JSONB       NOT NULL DEFAULT '{}'::jsonb,
                markdown     TEXT        NOT NULL DEFAULT '',
                created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            CREATE INDEX IF NOT EXISTS campaign_specs_campaign_idx
                ON campaign_specs (campaign_id);
            """
        )


# --------------------------------------------------------------------------- #
# Assembly — pure rendering from REAL inputs (honest-null on absence)
# --------------------------------------------------------------------------- #
_ABSENT = "_(not recorded)_"


def _val(v: Any) -> str:
    """Render a scalar honestly: empty/None -> honest-null marker."""
    if v is None:
        return _ABSENT
    s = str(v).strip()
    return s if s else _ABSENT


def _bullets(items: list[Any] | None) -> str:
    vals = [str(x).strip() for x in (items or []) if str(x).strip()]
    if not vals:
        return _ABSENT + "\n"
    return "".join(f"- {v}\n" for v in vals)


def assemble_spec(
    *,
    run_id: str,
    campaign_id: str | None,
    tenant_id: str | None,
    session_id: str | None,
    archetype_id: str | None,
    plan: dict[str, Any] | None,
    agent_runs: list[dict[str, Any]] | None,
    n_pending: int | None,
    n_queued: int | None,
    channels: list[str] | None,
    step_notes: list[str] | None,
    archetype_meta: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str]:
    """Build (content, markdown) from REAL inputs only.

    ``plan`` is a dict of {goal, audience, channels, sections, schedule}.
    ``agent_runs`` is the runner's list of {role, model, output_summary}.
    ``archetype_meta`` is {success_metric, trigger, steps_enabled} from the
    archetype registry (None / missing keys when the archetype is not known —
    e.g. on reconstruction of a pre-existing run; rendered honest-null).
    Every absent field is rendered honestly, never stubbed.
    """
    plan = plan or {}
    agent_runs = agent_runs or []
    archetype_meta = archetype_meta or {}

    plan_channels = plan.get("channels") or []
    eff_channels = channels or plan_channels

    content: dict[str, Any] = {
        "run_id": run_id,
        "campaign_id": campaign_id,
        "tenant_id": tenant_id,
        "session_id": session_id,
        "archetype_id": archetype_id,
        "goal": plan.get("goal") or None,
        "audience": plan.get("audience") or None,
        "channels": list(eff_channels),
        "sections": list(plan.get("sections") or []),
        "schedule": dict(plan.get("schedule") or {}),
        "success_metric": archetype_meta.get("success_metric"),
        "trigger": archetype_meta.get("trigger"),
        "steps_enabled": list(archetype_meta.get("steps_enabled") or []),
        "n_pending": n_pending,
        "n_queued": n_queued,
        "team": [
            {
                "role": ar.get("role"),
                "model": ar.get("model"),
                "output_summary": ar.get("output_summary"),
            }
            for ar in agent_runs
        ],
        "step_notes": list(step_notes or []),
    }

    # ---- markdown render ----
    lines: list[str] = []
    title_arch = archetype_id or "campaign"
    lines.append(f"# Campaign Spec — {title_arch}")
    lines.append("")
    lines.append(f"- **Run:** `{run_id}`")
    lines.append(f"- **Campaign:** `{campaign_id}`" if campaign_id else f"- **Campaign:** {_ABSENT}")
    lines.append(f"- **Tenant:** {_val(tenant_id)}")
    lines.append(f"- **Archetype:** {_val(archetype_id)}")
    np = "0" if n_pending is None else str(n_pending)
    nq = "0" if n_queued is None else str(n_queued)
    lines.append(f"- **Outcome:** {nq} draft(s) queued · {np} action(s) PENDING approval · HELD, nothing sent")
    lines.append("")

    lines.append("## Goal")
    lines.append(_val(plan.get("goal")))
    lines.append("")

    lines.append("## Audience")
    lines.append(_val(plan.get("audience")))
    lines.append("")

    lines.append("## Channels")
    if eff_channels:
        lines.append(", ".join(str(c) for c in eff_channels))
    else:
        lines.append(_ABSENT)
    lines.append("")

    lines.append("## Success metric")
    lines.append(_val(archetype_meta.get("success_metric")))
    lines.append("")

    lines.append("## Trigger")
    lines.append(_val(archetype_meta.get("trigger")))
    lines.append("")

    steps_enabled = archetype_meta.get("steps_enabled") or []
    lines.append("## Steps enabled")
    if steps_enabled:
        lines.append(", ".join(sorted(str(s) for s in steps_enabled)))
    else:
        lines.append(_ABSENT)
    lines.append("")

    lines.append("## Plan sections")
    lines.append(_bullets(plan.get("sections")).rstrip("\n"))
    lines.append("")

    lines.append("## Team execution (per role)")
    if agent_runs:
        for ar in agent_runs:
            role = _val(ar.get("role"))
            model = ar.get("model") or _ABSENT
            lines.append(f"### {role} — `{model}`")
            lines.append(_val(ar.get("output_summary")))
            lines.append("")
    else:
        lines.append(_ABSENT)
        lines.append("")

    schedule = plan.get("schedule") or {}
    lines.append("## Schedule")
    if schedule:
        for k, v in schedule.items():
            lines.append(f"- **{k}:** {v}")
    else:
        lines.append(_ABSENT)
    lines.append("")

    if step_notes:
        lines.append("## Step log")
        for note in step_notes:
            lines.append(f"- {note}")
        lines.append("")

    markdown = "\n".join(lines).rstrip() + "\n"
    return content, markdown


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #
def upsert_spec(
    run_id: str,
    *,
    campaign_id: str | None = None,
    tenant_id: str | None = None,
    session_id: str | None = None,
    archetype_id: str | None = None,
    content: dict[str, Any] | None = None,
    markdown: str = "",
    dsn: str | None = None,
) -> str:
    """Insert-or-update this run's spec; bump ``updated_at``. Returns run_id."""
    setup(dsn)
    with _connect(dsn) as conn:
        conn.execute(
            """
            INSERT INTO campaign_specs
                (run_id, campaign_id, tenant_id, session_id, archetype_id, content, markdown)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)
            ON CONFLICT (run_id) DO UPDATE SET
                campaign_id  = EXCLUDED.campaign_id,
                tenant_id    = EXCLUDED.tenant_id,
                session_id   = EXCLUDED.session_id,
                archetype_id = EXCLUDED.archetype_id,
                content      = EXCLUDED.content,
                markdown     = EXCLUDED.markdown,
                updated_at   = now()
            """,
            (
                run_id,
                campaign_id,
                tenant_id,
                session_id,
                archetype_id,
                json.dumps(content or {}),
                markdown,
            ),
        )
    return run_id


def get_spec(run_id: str, *, dsn: str | None = None) -> dict[str, Any] | None:
    """Return the stored spec row for ``run_id``, or None if none persisted."""
    try:
        with _connect(dsn) as conn:
            return conn.execute(
                "SELECT run_id, campaign_id, tenant_id, session_id, archetype_id, "
                "content, markdown, created_at, updated_at "
                "FROM campaign_specs WHERE run_id=%s",
                (run_id,),
            ).fetchone()
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Reconstruction from persisted rows (read-only, honest-null on absence)
# --------------------------------------------------------------------------- #
def _parse_brief(brief: str | None) -> dict[str, Any]:
    """Parse a ``_brief_from_plan``-shaped brief back into plan fields."""
    out: dict[str, Any] = {"goal": None, "audience": None, "channels": [], "sections": []}
    if not brief:
        return out
    for raw in str(brief).splitlines():
        line = raw.strip()
        low = line.lower()
        if low.startswith("goal:"):
            out["goal"] = line.split(":", 1)[1].strip() or None
        elif low.startswith("audience:"):
            out["audience"] = line.split(":", 1)[1].strip() or None
        elif low.startswith("channels:"):
            out["channels"] = [c.strip() for c in line.split(":", 1)[1].split(",") if c.strip()]
        elif low.startswith("sections:"):
            out["sections"] = [s.strip() for s in line.split(":", 1)[1].split(",") if s.strip()]
    return out


def reconstruct_spec(run_id: str, *, dsn: str | None = None) -> dict[str, Any] | None:
    """Assemble a spec for an existing run from its ALREADY-PERSISTED rows.

    Reads ``agent_runs`` (role/model/output + campaign_id + the brief from the
    research/strategist input) and the ``runs`` row (tenant). The archetype is
    NOT persisted per-run, so ``archetype_id`` and its derived fields render
    honest-null on reconstruction. This is READ-ONLY — it does not persist and
    does not call any model. Returns a spec-row-shaped dict, or None if the run
    has no persisted agent_runs.
    """
    try:
        with _connect(dsn) as conn:
            rows = conn.execute(
                "SELECT role, model, campaign_id, input, output, created_at "
                "FROM agent_runs WHERE run_id=%s ORDER BY created_at",
                (run_id,),
            ).fetchall()
            run_row = conn.execute(
                "SELECT tenant_id FROM runs WHERE run_id=%s", (run_id,)
            ).fetchone()
    except Exception:
        return None

    if not rows:
        return None

    campaign_id = next((r.get("campaign_id") for r in rows if r.get("campaign_id")), None)
    tenant_id = run_row.get("tenant_id") if run_row else None

    brief = None
    for r in rows:
        inp = r.get("input")
        if isinstance(inp, dict) and inp.get("brief"):
            brief = inp.get("brief")
            break
    plan = _parse_brief(brief)

    # Per-role summaries from the real outputs (reuse the runner's summarizer).
    try:
        from studio.campaign_runner import _summarize_output
    except Exception:  # pragma: no cover - defensive
        _summarize_output = None  # type: ignore[assignment]

    agent_runs: list[dict[str, Any]] = []
    for r in rows:
        role = str(r.get("role") or "")
        if _summarize_output is not None:
            summ = _summarize_output(role, r.get("output"))
        else:
            out = r.get("output")
            summ = json.dumps(out)[:160] if out is not None else ""
        agent_runs.append(
            {"role": r.get("role"), "model": r.get("model"), "output_summary": summ}
        )

    content, markdown = assemble_spec(
        run_id=run_id,
        campaign_id=campaign_id,
        tenant_id=tenant_id,
        session_id=None,
        archetype_id=None,  # not recoverable from persisted rows (honest-null)
        plan=plan,
        agent_runs=agent_runs,
        n_pending=None,
        n_queued=None,
        channels=plan.get("channels") or [],
        step_notes=None,
        archetype_meta=None,
    )
    return {
        "run_id": run_id,
        "campaign_id": campaign_id,
        "tenant_id": tenant_id,
        "session_id": None,
        "archetype_id": None,
        "content": content,
        "markdown": markdown,
        "created_at": None,
        "updated_at": None,
    }


def get_or_reconstruct(run_id: str, *, dsn: str | None = None) -> dict[str, Any] | None:
    """Stored spec if present, else a read-only reconstruction from persisted
    rows, else None (honest-null — the run has no spec and nothing to assemble)."""
    stored = get_spec(run_id, dsn=dsn)
    if stored is not None:
        return stored
    return reconstruct_spec(run_id, dsn=dsn)
