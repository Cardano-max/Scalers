"""The ``context_artifacts`` store — the UNIVERSAL uploaded-file registry (nmh.4).

Every file the operator uploads — a customer CSV, a brand-voice doc, a PDF/DOCX,
an image, an artwork asset, a campaign screenshot — is registered here as ONE
accessible *context artifact* carrying: name, type, parsed content, image
preview, source, created time, and the entity it is linked to. This is the
single source of truth for "what files exist" that BOTH the realtime voice
supervisor AND every campaign agent read, so the supervisor can truthfully
answer "can you see the customer CSV / the brand voice / the artwork — how many
images are uploaded?" from REAL state instead of guessing (product spec §2, §17).

It does NOT replace the stores it unifies (``tenant_documents`` for doc chunks,
``customers`` for CSV rows, the ``assets`` table for artwork JSONB): an artifact
row is the unified INDEX + a pointer (``meta['document_id']`` etc.) + the parsed
content/preview needed to answer state questions and ground agents in a run.

Thin psycopg layer over ``infra/initdb/20-context-artifacts.sql`` (the single
source of truth for the schema), DSN from ``ENGINE_DATABASE_URL`` — mirrors
:mod:`studio.documents` exactly (tenant-scoped, soft-remove via ``active``,
idempotent ``ensure_schema``).

HONESTY GATE: every read returns only real rows. :func:`list_artifacts` /
:func:`artifact_inventory` over a tenant with no artifacts return an empty
list / all-zero counts — never a fabricated file. Removal is a soft
``active=false`` so the artifact drops from every agent surface immediately.
An image whose visual content has not been captioned yet has an empty
``parsed_content`` and says so — it is never given an invented description.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_DEFAULT_DSN = "postgresql://scalers:scalers@localhost:5432/scalers"

# infra/initdb/20-context-artifacts.sql relative to this file
# (engine/studio/artifacts.py): parents[0]=studio, [1]=engine, [2]=repo root.
_ARTIFACTS_SQL = (
    Path(__file__).resolve().parents[2] / "infra" / "initdb" / "20-context-artifacts.sql"
)

# The coarse kinds the supervisor counts by (mirror the SQL CHECK constraint so a
# bad type fails in Python with a clear message rather than a DB 23514).
VALID_ARTIFACT_TYPES: frozenset[str] = frozenset(
    {"csv", "brand_voice", "document", "pdf", "image", "artwork", "screenshot", "video", "other"}
)
_IMAGE_TYPES: frozenset[str] = frozenset({"image", "artwork", "screenshot"})

# A stored image preview is a data-uri; cap it so a huge upload never bloats the
# per-turn context or the row. Above the cap we keep the artifact (countable +
# listable) but drop the inline preview and note it in meta.
_PREVIEW_MAX_CHARS = 200_000


def _dsn(dsn: str | None = None) -> str:
    return dsn or os.environ.get("ENGINE_DATABASE_URL") or _DEFAULT_DSN


def _connect(dsn: str | None = None):
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(_dsn(dsn), autocommit=True, row_factory=dict_row)


_SCHEMA_READY: set[str] = set()


def ensure_schema(dsn: str | None = None) -> None:
    """Apply ``20-context-artifacts.sql`` (idempotent ``CREATE TABLE IF NOT EXISTS``).

    Once per process per DSN: the DDL is idempotent but NOT concurrency-free —
    parallel ``CREATE TABLE IF NOT EXISTS``/``ALTER`` from simultaneous reads
    deadlock in Postgres (observed as intermittent 500s on artifact reads under
    concurrent console traffic). After the first successful apply, later calls
    are no-ops; a failure stays unrecorded so the next call retries."""
    key = _dsn(dsn)
    if key in _SCHEMA_READY:
        return
    with _connect(dsn) as conn:
        conn.execute(_ARTIFACTS_SQL.read_text(encoding="utf-8"))
    _SCHEMA_READY.add(key)


# --------------------------------------------------------------------------- #
# Writes.
# --------------------------------------------------------------------------- #
def register_artifact(
    tenant_id: str,
    name: str,
    artifact_type: str,
    *,
    media_type: str | None = None,
    summary: str | None = None,
    parsed_content: str | None = None,
    preview: str | None = None,
    source: str = "upload",
    linked_entity_type: str | None = None,
    linked_entity_id: str | None = None,
    meta: dict[str, Any] | None = None,
    artifact_id: str | None = None,
    dsn: str | None = None,
) -> str:
    """Register (idempotently upsert) one uploaded file as a context artifact; return
    its id.

    Idempotent on a supplied ``artifact_id`` (``ON CONFLICT (id) DO UPDATE``): a
    re-upload of the same logical file REFRESHES its content/summary/preview rather
    than duplicating — so re-parsing a CSV or re-captioning an image updates in
    place. An oversize image ``preview`` is dropped (the artifact still registers,
    countable + listable) and noted in ``meta['preview_omitted']`` — honest, never a
    fabricated thumbnail."""
    if artifact_type not in VALID_ARTIFACT_TYPES:
        raise ValueError(f"artifact_type {artifact_type!r} not in {sorted(VALID_ARTIFACT_TYPES)}")
    if not (name or "").strip():
        raise ValueError("artifact name is empty")
    if linked_entity_type is not None and linked_entity_type not in (
        "campaign",
        "artist",
        "customer",
    ):
        raise ValueError(f"linked_entity_type {linked_entity_type!r} invalid")

    from psycopg.types.json import Json

    meta = dict(meta or {})
    if preview is not None and len(preview) > _PREVIEW_MAX_CHARS:
        meta["preview_omitted"] = f"preview {len(preview)} chars exceeds {_PREVIEW_MAX_CHARS} cap"
        preview = None
    art_id = artifact_id or f"art_{uuid.uuid4().hex[:16]}"
    ensure_schema(dsn)
    with _connect(dsn) as conn:
        row = conn.execute(
            """
            INSERT INTO context_artifacts
                (id, tenant_id, name, artifact_type, media_type, summary,
                 parsed_content, preview, source, linked_entity_type,
                 linked_entity_id, meta, active)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,TRUE)
            ON CONFLICT (id) DO UPDATE SET
                name = EXCLUDED.name,
                artifact_type = EXCLUDED.artifact_type,
                media_type = EXCLUDED.media_type,
                summary = EXCLUDED.summary,
                parsed_content = EXCLUDED.parsed_content,
                preview = EXCLUDED.preview,
                source = EXCLUDED.source,
                linked_entity_type = EXCLUDED.linked_entity_type,
                linked_entity_id = EXCLUDED.linked_entity_id,
                meta = EXCLUDED.meta,
                active = TRUE,
                updated_at = now()
            -- Tenant-scoped: a conflicting id under a DIFFERENT tenant is NOT
            -- clobbered (defense-in-depth; ids are already random/tenant-derived
            -- so this is unreachable in practice, but the guard makes a cross-
            -- tenant collision a safe no-op rather than a content overwrite).
            WHERE context_artifacts.tenant_id = EXCLUDED.tenant_id
            RETURNING id
            """,
            (
                art_id,
                tenant_id,
                name.strip(),
                artifact_type,
                media_type,
                summary,
                parsed_content,
                preview,
                source,
                linked_entity_type,
                linked_entity_id,
                Json(meta),
            ),
        ).fetchone()
    # A None row means the id existed under another tenant and the guard blocked
    # the update — return the attempted id (the caller's write was a safe no-op;
    # the other tenant's row is untouched and invisible to this caller anyway).
    return row["id"] if row else art_id


def deactivate_artifact(tenant_id: str, artifact_id: str, *, dsn: str | None = None) -> bool:
    """Soft-remove an artifact (``active=false``) so it drops from EVERY agent surface.

    Returns True if a currently-active artifact was deactivated, False if it did not
    exist / was already inactive / belongs to another tenant (real-only)."""
    ensure_schema(dsn)
    with _connect(dsn) as conn:
        row = conn.execute(
            "UPDATE context_artifacts SET active=FALSE, updated_at=now() "
            "WHERE id=%s AND tenant_id=%s AND active=TRUE RETURNING id",
            (artifact_id, tenant_id),
        ).fetchone()
    return row is not None


# --------------------------------------------------------------------------- #
# Reads.
# --------------------------------------------------------------------------- #
def get_artifact(artifact_id: str, *, dsn: str | None = None) -> dict[str, Any] | None:
    """Full artifact row (incl. parsed_content + preview), or None when absent.

    This is the accessor a campaign AGENT uses to read an artifact's parsed content
    inside a run (nmh.4 AC: "other agents can access the parsed content in a run")."""
    ensure_schema(dsn)
    with _connect(dsn) as conn:
        row = conn.execute(
            "SELECT id, tenant_id, name, artifact_type, media_type, summary, "
            "parsed_content, preview, source, linked_entity_type, linked_entity_id, "
            "meta, active, created_at "
            "FROM context_artifacts WHERE id=%s",
            (artifact_id,),
        ).fetchone()
    return dict(row) if row else None


def list_artifacts(
    tenant_id: str,
    *,
    active_only: bool = True,
    artifact_type: str | None = None,
    include_content: bool = False,
    content_limit: int | None = None,
    dsn: str | None = None,
) -> list[dict[str, Any]]:
    """The tenant's artifacts (newest first) as a compact index: id, name,
    artifact_type, media_type, summary, source, linked entity, meta, active,
    created_at. ``[]`` when none (honest). ``include_content=True`` also returns
    ``parsed_content`` (and whether a preview exists) so an agent can read the text
    of every uploaded file in one call; the (potentially large) preview data-uri is
    fetched per-artifact via :func:`get_artifact`, never in the list.

    ``content_limit`` bounds the returned ``parsed_content`` to the first N chars
    IN SQL (``left(...)``) — the per-turn context builder uses this so a 500-row
    CSV's full text is never pulled into every host turn just to show an excerpt.
    Omit it (full content) only when an agent genuinely needs the whole file."""
    ensure_schema(dsn)
    clauses = ["tenant_id=%s"]
    params: list[Any] = [tenant_id]
    if active_only:
        clauses.append("active=TRUE")
    if artifact_type is not None:
        clauses.append("artifact_type=%s")
        params.append(artifact_type)
    if include_content:
        content_expr = (
            f"left(parsed_content, {int(content_limit)})" if content_limit else "parsed_content"
        )
        content_col = f", {content_expr} AS parsed_content, (preview IS NOT NULL) AS has_preview"
    else:
        content_col = ""
    with _connect(dsn) as conn:
        rows = conn.execute(
            f"SELECT id, name, artifact_type, media_type, summary, source, "
            f"linked_entity_type, linked_entity_id, meta, active, created_at{content_col} "
            f"FROM context_artifacts WHERE {' AND '.join(clauses)} "
            f"ORDER BY created_at DESC",
            tuple(params),
        ).fetchall()
    return [dict(r) for r in rows]


@dataclass(frozen=True)
class ArtifactInventory:
    """The real uploaded-file inventory for one tenant — every number a live count.

    ``by_type`` maps artifact_type -> count; ``images`` is the sum of image-ish
    types (image + artwork + screenshot), the number the supervisor answers "how
    many images" from. ``readable`` is False only when the store could not be read
    (distinct from a genuine empty tenant)."""

    tenant_id: str
    total: int = 0
    by_type: dict[str, int] = field(default_factory=dict)
    names_by_type: dict[str, list[str]] = field(default_factory=dict)
    readable: bool = True

    @property
    def images(self) -> int:
        return sum(self.by_type.get(t, 0) for t in _IMAGE_TYPES)


def artifact_inventory(tenant_id: str, *, dsn: str | None = None) -> ArtifactInventory:
    """Live counts of the tenant's ACTIVE artifacts, grouped by type — the real
    state the supervisor answers file/image questions from. Best-effort: a store it
    cannot read yields ``readable=False`` (never a fabricated zero)."""
    try:
        rows = list_artifacts(tenant_id, active_only=True, dsn=dsn)
    except Exception:
        return ArtifactInventory(tenant_id=tenant_id, readable=False)
    by_type: dict[str, int] = {}
    names_by_type: dict[str, list[str]] = {}
    for r in rows:
        t = r["artifact_type"]
        by_type[t] = by_type.get(t, 0) + 1
        names_by_type.setdefault(t, []).append(r["name"])
    return ArtifactInventory(
        tenant_id=tenant_id, total=len(rows), by_type=by_type, names_by_type=names_by_type
    )


# --------------------------------------------------------------------------- #
# Human-readable labels for the readback / context blocks.
# --------------------------------------------------------------------------- #
_TYPE_LABEL: dict[str, str] = {
    "csv": "customer CSV",
    "brand_voice": "brand-voice file",
    "document": "document",
    "pdf": "PDF",
    "image": "image",
    "video": "video (frame-sampled)",
    "artwork": "artwork image",
    "screenshot": "campaign screenshot",
    "other": "file",
}


def _plural(label: str, n: int) -> str:
    return label if n == 1 else (label[:-1] + "ies" if label.endswith("y") else label + "s")


def build_artifacts_readback(inv: ArtifactInventory) -> str:
    """The honest uploaded-files readback for ``inv`` — pure, no I/O. States the real
    counts by type + the total image count, so the supervisor answers "can you see
    the CSV / how many images" from here. Empty tenant → an explicit "no files
    uploaded yet" line (never a fabricated file)."""
    if not inv.readable:
        return (
            "UPLOADED FILES: I could not read the file store this turn, so I will not "
            "quote a file count — ask me to try again rather than have me guess."
        )
    if inv.total == 0:
        return (
            "UPLOADED FILES: no files are uploaded for this studio yet. If the operator "
            "asks whether you can see a CSV / brand voice / image, say honestly that "
            "none are uploaded yet and invite them to add one — never claim a file you "
            "do not have."
        )
    bits: list[str] = []
    for t in ("csv", "brand_voice", "document", "pdf", "image", "artwork", "screenshot", "video", "other"):
        n = inv.by_type.get(t, 0)
        if not n:
            continue
        names = inv.names_by_type.get(t, [])
        shown = ", ".join(names[:3]) + ("…" if len(names) > 3 else "")
        bits.append(f"{n} {_plural(_TYPE_LABEL[t], n)} ({shown})")
    lines = [
        "UPLOADED FILES — the REAL files on record for this studio, live from the "
        "registry (never estimated). You CAN see these; answer 'can you see the CSV / "
        "brand voice / artwork' and 'how many images' from HERE, and never claim a "
        "file that is not listed:",
        f"- {inv.total} file(s): " + "; ".join(bits),
        f"- images uploaded: {inv.images}",
    ]
    return "\n".join(lines)


def build_artifacts_context(tenant_id: str, *, dsn: str | None = None) -> str:
    """The per-turn artifacts block injected into the host + every campaign agent so
    they can SEE the uploaded files and reference their parsed content in a run
    (nmh.4 AC: "other agents can access the parsed content in a run"). Lists each
    active artifact by name + type + summary, and includes a bounded excerpt of the
    parsed content for text artifacts so a cell can ground on it directly. Honest:
    with no artifacts it says so; an unreadable store degrades to a neutral note."""
    try:
        # Bounded fetch: only the first 600 chars of each file's parsed content are
        # pulled (we show a 400-char excerpt) — a 500-row CSV's full text never
        # enters the per-turn host context (S1). Full content is via get_artifact.
        arts = list_artifacts(
            tenant_id, active_only=True, include_content=True, content_limit=600, dsn=dsn
        )
    except Exception:
        return (
            "UPLOADED FILES: the file registry is configured; uploads survive across "
            "sessions and the whole team reads them. (No files loaded this turn.)"
        )
    if not arts:
        return (
            "UPLOADED FILES: no files uploaded for this studio yet. Never claim to see "
            "a file you do not have."
        )
    lines = [
        "UPLOADED FILES — you HAVE these files and the whole team can read them. When "
        "asked 'do you see my CSV / brand voice / artwork', answer YES and name them; "
        "ground drafts in their parsed content where relevant:",
    ]
    for a in arts:
        label = _TYPE_LABEL.get(a["artifact_type"], "file")
        summ = (a.get("summary") or "").strip()
        head = f"- {a['name']} [{label}]" + (f" — {summ}" if summ else "")
        lines.append(head)
        content = (a.get("parsed_content") or "").strip()
        if content:
            excerpt = content[:400].replace("\n", " ")
            lines.append(
                f"    parsed content (excerpt): {excerpt}" + ("…" if len(content) > 400 else "")
            )
        elif a["artifact_type"] in _IMAGE_TYPES:
            lines.append(
                "    (image on file — visual understanding not captured yet; do NOT "
                "invent what it depicts)"
            )
    return "\n".join(lines)
