"""The ``tenant_documents`` store — the PERSISTENT per-tenant knowledge layer.

This is the durable document store that EVERY agent reads RAG-style: the AG-UI
host/supervisor (per-turn context), the LangGraph orchestration nodes (per-task
retrieval), and the realtime voice supervisor (session instructions). A doc
uploaded here SURVIVES sessions/runs — it is tenant-scoped, NOT tied to a chat
session id — and deactivating it (``active=false``) drops it from every surface at
once.

Thin psycopg layer over ``infra/initdb/11-tenant-documents.sql`` (the single source
of truth for the schema), DSN from ``ENGINE_DATABASE_URL`` — the same pattern as
:mod:`actions.audit` (``10-send-audit.sql``) and :mod:`research.sources_store`.

RETRIEVAL is Postgres-native full-text search: each doc is chunked into passages
with a generated ``tsvector`` column + GIN index, and :func:`retrieve` ranks the
ACTIVE tenant's chunks by ``ts_rank`` for a query. No pgvector / embeddings — robust
and dependency-free.

HONESTY GATE: every read returns only real rows. :func:`list_documents` /
:func:`active_docs_index` / :func:`retrieve` over a tenant with no active docs
return ``[]`` — never a fabricated document. Removal is a soft ``active=false`` so
the doc is invisible to every agent immediately.
"""

from __future__ import annotations

import csv
import io
import os
import re
import uuid
from pathlib import Path
from typing import Any

_DEFAULT_DSN = "postgresql://scalers:scalers@localhost:5432/scalers"

# infra/initdb/11-tenant-documents.sql relative to this file
# (engine/studio/documents.py): parents[0]=studio, [1]=engine, [2]=repo root.
_DOCS_SQL = (
    Path(__file__).resolve().parents[2] / "infra" / "initdb" / "11-tenant-documents.sql"
)

# Chunking budget: target passage size (chars) and a hard ceiling, so one passage
# never blows the context window of the node that retrieves it.
_CHUNK_TARGET = 900
_CHUNK_HARD_MAX = 1500


def _dsn(dsn: str | None = None) -> str:
    return dsn or os.environ.get("ENGINE_DATABASE_URL") or _DEFAULT_DSN


def _connect(dsn: str | None = None):
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(_dsn(dsn), autocommit=True, row_factory=dict_row)


def ensure_schema(dsn: str | None = None) -> None:
    """Apply ``11-tenant-documents.sql`` (idempotent ``CREATE TABLE IF NOT EXISTS``)."""
    with _connect(dsn) as conn:
        conn.execute(_DOCS_SQL.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# Pure helpers — chunking + summary (no I/O, unit-testable without Postgres).
# --------------------------------------------------------------------------- #
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.*\S)\s*$")


def _is_heading(line: str) -> str | None:
    """Return the heading text if ``line`` is a markdown heading, else None."""
    m = _HEADING_RE.match(line)
    return m.group(1).strip() if m else None


def _split_oversize(text: str, hard_max: int) -> list[str]:
    """Split a too-long passage on sentence/word boundaries into <= ``hard_max`` pieces."""
    text = text.strip()
    if len(text) <= hard_max:
        return [text] if text else []
    out: list[str] = []
    buf = ""
    # Prefer sentence boundaries; fall back to hard slicing for pathological input.
    for piece in re.split(r"(?<=[.!?])\s+", text):
        if not piece:
            continue
        if len(buf) + len(piece) + 1 > hard_max and buf:
            out.append(buf.strip())
            buf = ""
        if len(piece) > hard_max:
            for i in range(0, len(piece), hard_max):
                out.append(piece[i : i + hard_max].strip())
            continue
        buf = f"{buf} {piece}".strip()
    if buf.strip():
        out.append(buf.strip())
    return [p for p in out if p]


# --------------------------------------------------------------------------- #
# CSV-aware chunking — a CSV is structured rows, not prose. We emit ONE retrievable
# passage per data row (carrying the header context) so an individual lead/row is
# findable by ts_rank, instead of dumping the whole table as one opaque blob.
# --------------------------------------------------------------------------- #
def _parse_csv(content: str) -> tuple[list[str], list[list[str]]]:
    """Parse CSV ``content`` into ``(header, data_rows)``.

    The first non-empty record is the header; the rest are data rows. Returns
    ``([], [])`` for empty/headerless input (honest — caller falls back). Pure."""
    text = (content or "").replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    if not text.strip():
        return [], []
    rows = [
        [c.strip() for c in row]
        for row in csv.reader(io.StringIO(text))
        if any(c.strip() for c in row)  # drop fully-blank records
    ]
    if not rows:
        return [], []
    header = rows[0]
    return header, rows[1:]


def _looks_like_csv(content: str) -> bool:
    """Deliberately CONSERVATIVE CSV sniff — used ONLY when content arrives without an
    explicit ``kind`` (the panel always sends ``kind``, so this is a backend fallback
    for direct API callers). When in doubt it returns False so prose/markdown stays a
    plain doc — a brand playbook must NEVER be row-chunked.

    Requires strong, table-shaped signals, all of:
      - the content is not a markdown heading,
      - a delimited header of >= 2 columns,
      - MULTIPLE data rows (>= 2), not a single comma-bearing line, and
      - EXACT column-count consistency: every data row has the header's width.
    An explicit ``kind=='csv'`` upload bypasses this entirely and is always row-chunked,
    so a genuinely ragged CSV picked in the UI is still honored."""
    text = (content or "").strip()
    if not text or text.lstrip().startswith("#"):
        return False
    header, data = _parse_csv(text)
    if len(header) < 2 or len(data) < 2:
        return False
    # A real table has a consistent column count across every row; a prose snippet
    # that merely contains commas does not. Demand exact agreement (strong signal).
    return all(len(r) == len(header) for r in data)


def _format_csv_row(header: list[str], row: list[str], rownum: int) -> str:
    """One row as a self-describing passage: ``Row N — col: val, col2: val2``.

    Header context travels with every row so the cell values are retrievable in
    isolation. Empty cells are dropped to keep the passage focused; extra values
    beyond the header are labelled ``colK``."""
    parts: list[str] = []
    for i, val in enumerate(row):
        if not val:
            continue
        col = header[i] if i < len(header) and header[i] else f"col{i + 1}"
        parts.append(f"{col}: {val}")
    body = ", ".join(parts)
    return f"Row {rownum} — {body}" if body else f"Row {rownum}"


def _chunk_csv(
    content: str, *, hard_max: int = _CHUNK_HARD_MAX
) -> list[tuple[str | None, str]]:
    """Chunk a CSV into one ``(heading, passage)`` per data row (header carried on
    each). A pathologically wide row is split on boundaries to respect ``hard_max``;
    its ``Row N`` heading is kept on every piece. Pure — no I/O."""
    header, data = _parse_csv(content)
    if not header or not data:
        return []
    chunks: list[tuple[str | None, str]] = []
    for idx, row in enumerate(data, start=1):
        heading = f"Row {idx}"
        passage = _format_csv_row(header, row, idx)
        if len(passage) <= hard_max:
            chunks.append((heading, passage))
        else:
            for piece in _split_oversize(passage, hard_max):
                chunks.append((heading, piece))
    return chunks


def _summarize_csv(content: str) -> str:
    """Truthful CSV summary: ``CSV: <N> rows; columns: <a, b, c>`` with REAL counts
    (data rows, excluding the header) and the real column names. Pure — never fabricates."""
    header, data = _parse_csv(content)
    if not header:
        return ""
    cols = ", ".join(c for c in header if c) or "(unnamed)"
    n = len(data)
    return f"CSV: {n} {'row' if n == 1 else 'rows'}; columns: {cols}"


def _is_csv_kind(kind: str | None, content: str) -> bool:
    """Whether ``content`` should be chunked/summarized as CSV: an explicit
    ``kind=='csv'``, or (when no kind is given) a conservative content sniff."""
    if (kind or "").strip().lower() == "csv":
        return True
    return kind in (None, "") and _looks_like_csv(content)


def chunk_document(
    content: str,
    *,
    kind: str | None = None,
    target: int = _CHUNK_TARGET,
    hard_max: int = _CHUNK_HARD_MAX,
) -> list[tuple[str | None, str]]:
    """Split a document into retrievable passages, returning ``(heading, text)`` pairs.

    CSV-aware: when ``kind=='csv'`` (or, with no kind, the content sniffs as CSV) the
    document is chunked one passage PER ROW via :func:`_chunk_csv`, so an individual
    lead/row is retrievable by ts_rank rather than buried in one blob.

    Otherwise markdown-aware: it tracks the nearest section heading and groups
    paragraphs under it until the passage reaches ~``target`` chars (flushing at a
    paragraph boundary), flushing early on a heading change. The heading is carried on
    each chunk for citations. A single oversize paragraph is split on sentence
    boundaries to respect ``hard_max``. Pure — no I/O."""
    if _is_csv_kind(kind, content):
        return _chunk_csv(content, hard_max=hard_max)
    text = (content or "").replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        return []

    chunks: list[tuple[str | None, str]] = []
    current_heading: str | None = None
    buf_lines: list[str] = []

    def buf_len() -> int:
        return sum(len(x) + 1 for x in buf_lines)

    def flush() -> None:
        body = "\n".join(buf_lines).strip()
        buf_lines.clear()
        if not body:
            return
        for piece in _split_oversize(body, hard_max):
            chunks.append((current_heading, piece))

    # Iterate by paragraph (blank-line separated), but keep heading lines as their
    # own markers so a heading change flushes the prior section.
    paragraphs = re.split(r"\n\s*\n", text)
    for para in paragraphs:
        para = para.strip("\n")
        if not para.strip():
            continue
        heading = _is_heading(para.strip())
        if heading is not None:
            flush()
            current_heading = heading
            continue
        buf_lines.append(para)
        if buf_len() >= target:
            flush()
    flush()
    return chunks


def summarize(content: str, *, limit: int = 320, kind: str | None = None) -> str:
    """A compact, human-readable summary for the per-turn index.

    For a CSV (``kind=='csv'`` or sniffed) this is the truthful ``CSV: <N> rows;
    columns: <...>`` shape with REAL counts. Otherwise the first substantive
    (non-heading) paragraph, trimmed to ``limit`` chars. Pure — no model call."""
    if _is_csv_kind(kind, content):
        s = _summarize_csv(content)
        if s:
            return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"
    text = (content or "").replace("\r\n", "\n").replace("\r", "\n")
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para or _is_heading(para) is not None:
            continue
        # Collapse internal whitespace for a clean one-liner.
        one = re.sub(r"\s+", " ", para)
        return one if len(one) <= limit else one[: limit - 1].rstrip() + "…"
    # All-heading / empty body: fall back to the first non-empty line.
    for line in text.splitlines():
        s = re.sub(r"\s+", " ", line).strip().lstrip("#").strip()
        if s:
            return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"
    return ""


# --------------------------------------------------------------------------- #
# Writes.
# --------------------------------------------------------------------------- #
def add_document(
    tenant_id: str,
    name: str,
    content: str,
    *,
    kind: str = "doc",
    summary: str | None = None,
    source: str = "upload",
    doc_id: str | None = None,
    dsn: str | None = None,
) -> str:
    """Persist a document + its chunks; return the document id.

    Idempotent on a supplied ``doc_id`` (``ON CONFLICT (id) DO NOTHING``): if the row
    already exists the chunks are NOT re-inserted (used by the seed). The ``tsvector``
    column is generated by Postgres, so it is never written here."""
    ensure_schema(dsn)
    did = doc_id or f"doc_{uuid.uuid4().hex[:16]}"
    summ = summary if summary is not None else summarize(content, kind=kind)
    chunks = chunk_document(content, kind=kind)
    with _connect(dsn) as conn, conn.transaction():
        row = conn.execute(
            "INSERT INTO tenant_documents "
            "(id, tenant_id, name, kind, content, summary, active, source) "
            "VALUES (%s,%s,%s,%s,%s,%s,TRUE,%s) "
            "ON CONFLICT (id) DO NOTHING RETURNING id",
            (did, tenant_id, name, kind, content, summ, source),
        ).fetchone()
        if row is None:
            return did  # already existed — do not duplicate chunks
        for i, (heading, ctext) in enumerate(chunks):
            conn.execute(
                "INSERT INTO tenant_document_chunks "
                "(id, document_id, tenant_id, seq, heading, content) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (f"chk_{uuid.uuid4().hex[:16]}", did, tenant_id, i, heading, ctext),
            )
    return did


def deactivate_document(
    tenant_id: str, document_id: str, *, dsn: str | None = None
) -> bool:
    """Soft-remove a document (``active=false``) so it drops from EVERY agent surface.

    Returns True if a currently-active doc was deactivated, False if it did not exist
    / was already inactive / belongs to another tenant (real-only, no silent success)."""
    ensure_schema(dsn)
    with _connect(dsn) as conn:
        row = conn.execute(
            "UPDATE tenant_documents SET active=FALSE, updated_at=now() "
            "WHERE id=%s AND tenant_id=%s AND active=TRUE RETURNING id",
            (document_id, tenant_id),
        ).fetchone()
    return row is not None


# --------------------------------------------------------------------------- #
# Reads.
# --------------------------------------------------------------------------- #
def get_document(document_id: str, *, dsn: str | None = None) -> dict[str, Any] | None:
    """Full document row (incl. content + chunk count), or None when absent."""
    ensure_schema(dsn)
    with _connect(dsn) as conn:
        row = conn.execute(
            "SELECT id, tenant_id, name, kind, content, summary, active, source, "
            "created_at, length(content) AS chars, "
            "(SELECT count(*) FROM tenant_document_chunks c WHERE c.document_id=tenant_documents.id) AS chunks "
            "FROM tenant_documents WHERE id=%s",
            (document_id,),
        ).fetchone()
    return dict(row) if row else None


def list_documents(
    tenant_id: str, *, active_only: bool = True, dsn: str | None = None
) -> list[dict[str, Any]]:
    """The tenant's documents (newest first) as a compact index: id, name, kind,
    summary, char count, chunk count, active, created_at. ``[]`` when none (honest)."""
    ensure_schema(dsn)
    clause = "AND active=TRUE" if active_only else ""
    with _connect(dsn) as conn:
        rows = conn.execute(
            "SELECT id, name, kind, summary, active, source, created_at, "
            "length(content) AS chars, "
            "(SELECT count(*) FROM tenant_document_chunks c WHERE c.document_id=d.id) AS chunks "
            f"FROM tenant_documents d WHERE tenant_id=%s {clause} "
            "ORDER BY created_at DESC",
            (tenant_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def active_docs_index(
    tenant_id: str, *, dsn: str | None = None
) -> list[dict[str, Any]]:
    """The ACTIVE documents only — the compact per-turn index the agents advertise."""
    return list_documents(tenant_id, active_only=True, dsn=dsn)


def retrieve(
    tenant_id: str, query: str, k: int = 5, *, dsn: str | None = None
) -> list[dict[str, Any]]:
    """Top-k relevant passages for ``query`` across the tenant's ACTIVE docs, ranked by
    ``ts_rank`` (Postgres full-text). Each hit: ``{document_id, doc_name, kind, heading,
    content, seq, rank}``.

    HONESTY: returns ``[]`` when the query has no lexical match (or is empty / only
    stopwords) — never a forced or fabricated passage. Inactive docs are excluded by
    the join, so a removed doc is unreachable."""
    q = (query or "").strip()
    if not q:
        return []
    ensure_schema(dsn)
    k = max(1, min(int(k or 5), 20))
    with _connect(dsn) as conn:
        rows = conn.execute(
            "SELECT c.document_id, d.name AS doc_name, d.kind, c.heading, c.content, "
            "c.seq, ts_rank(c.tsv, plainto_tsquery('english', %s)) AS rank "
            "FROM tenant_document_chunks c "
            "JOIN tenant_documents d ON d.id = c.document_id "
            "WHERE d.tenant_id = %s AND d.active = TRUE "
            "AND c.tsv @@ plainto_tsquery('english', %s) "
            "ORDER BY rank DESC, c.seq ASC LIMIT %s",
            (q, tenant_id, q, k),
        ).fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------- #
# Seed — the operator's Ladies First brand playbook, packaged with the engine so
# the demo has a real active doc on first run regardless of which worktree runs.
# --------------------------------------------------------------------------- #
_SEED_FILE = Path(__file__).resolve().parent / "seed_docs" / "ladies-first-brand-playbook.md"
_SEED_NAME = "Ladies First Brand & Campaign Playbook"

# The packaged playbook is the FIXTURE studio's brand doc (Ladies First). It may ONLY
# ever seed a fixture/demo tenant — never a real client, whose RAG must not be polluted
# by another studio's playbook. Seeding it into skindesign's document store would make
# the Ladies First voice retrievable under the real client's name. Gate to this
# allowlist (r8: kill ladies8391 fixture bleed — seed_tenant_documents('skindesign')
# returns None). "demo" is the studio host's default STUDIO_TENANT_ID (agui) — a
# demo sandbox, not a real client — so its first-run doc seeding keeps working.
_FIXTURE_SEED_TENANTS = frozenset({"ladies8391", "ink-studio", "demo"})


def _seed_doc_id(tenant_id: str) -> str:
    """A deterministic seed id so re-seeding is a no-op (idempotent ON CONFLICT)."""
    return f"doc_seed_{tenant_id}_ladies_first_playbook"


def seed_tenant_documents(tenant_id: str, *, dsn: str | None = None) -> str | None:
    """Best-effort: load the packaged Ladies First brand playbook as the tenant's
    first ACTIVE document, so the operator has a real doc to point at immediately.

    Idempotent (deterministic id + ``ON CONFLICT DO NOTHING``). Returns the doc id on
    seed/exists, or None if the tenant is not a fixture (the playbook is the fixture
    studio's brand doc and must never seed a real client's RAG) or the packaged file is
    missing (honest — never fabricates a doc). Marked ``kind='brand'``, ``source='seed'``
    so it is clearly the operator's uploaded brand doc."""
    if tenant_id not in _FIXTURE_SEED_TENANTS:
        # Real client (e.g. skindesign): NEVER seed the fixture playbook into its RAG.
        return None
    if not _SEED_FILE.exists():
        return None
    content = _SEED_FILE.read_text(encoding="utf-8")
    if not content.strip():
        return None
    return add_document(
        tenant_id,
        _SEED_NAME,
        content,
        kind="brand",
        source="seed",
        doc_id=_seed_doc_id(tenant_id),
        dsn=dsn,
    )
