"""Load a context artifact's BINARY content for delivery (gmail attachment / IG media).

The studio staging path (parallel worktree) writes ``context.attachment_artifact_id``
(+ ``context.artwork = {assetId, artifactId, vlmSummary}``) on an action's ``context``
JSON, and gives ``context_artifacts`` rows an on-disk ``storage_path``
(``var/artifacts/...``). NONE of that is guaranteed to exist yet, so this module is
deliberately DEFENSIVE:

* the row is read with ``SELECT *`` so a ``storage_path`` column (or any later
  addition) is picked up whether it lands as a real column or a ``meta`` key;
* content resolution tries, in order: (1) ``storage_path`` on disk, (2) an explicit
  base64 content column/meta key (``content_b64`` / ``content_base64`` / ``content``),
  (3) the legacy ``preview`` data-uri (the bounded thumbnail the registry already
  stores) — each source is recorded on the result so the audit says exactly what
  bytes were used;
* every failure raises :class:`ArtifactMediaError` with a CONCRETE reason. The
  caller decides the policy: an action that never promised an artifact simply skips
  this module (graceful no-op); an action that DID promise one must fail closed on
  this error — never silently send without it.

Content bytes are never logged; callers audit via sha256 (see
:class:`connectors.mail_message.AttachmentReceipt`).
"""

from __future__ import annotations

import base64
import binascii
import json
import mimetypes
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_DEFAULT_DSN = "postgresql://scalers:scalers@localhost:5432/scalers"

# engine/sideeffects/artifact_media.py -> parents[1]=engine, parents[2]=repo root.
_ENGINE_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT = Path(__file__).resolve().parents[2]

_DATA_URI_RE = re.compile(
    r"^data:(?P<mime>[\w.+-]+/[\w.+-]+)?(?:;charset=[\w-]+)?;base64,(?P<b64>.+)$",
    re.DOTALL,
)


class ArtifactMediaError(RuntimeError):
    """An artifact's binary content could not be loaded — carries the concrete
    reason (row missing, removed, no stored bytes, file gone, undecodable). The
    delivery path FAILS CLOSED on this for a promised attachment."""


@dataclass(frozen=True)
class ArtifactMedia:
    """One artifact's loaded bytes + the honest provenance of where they came from."""

    artifact_id: str
    filename: str
    mime_type: str | None
    content_bytes: bytes
    source: str  # 'storage_path' | 'base64_column' | 'preview_data_uri'

    def as_attachment(self) -> dict[str, Any]:
        """The ``{filename, content_bytes, mime_type}`` shape the mail connectors take."""
        return {
            "filename": self.filename,
            "content_bytes": self.content_bytes,
            "mime_type": self.mime_type or "",
        }


def _fetch_artifact_row(artifact_id: str, dsn: str | None = None) -> dict[str, Any] | None:
    """Read ONE ``context_artifacts`` row as a plain dict (``SELECT *`` so columns
    added by the parallel studio worktree — e.g. ``storage_path`` — are visible
    without a code change here)."""
    import psycopg
    from psycopg.rows import dict_row

    conn_dsn = dsn or os.environ.get("ENGINE_DATABASE_URL") or _DEFAULT_DSN
    with psycopg.connect(conn_dsn, autocommit=True, row_factory=dict_row) as conn:
        row = conn.execute(
            "SELECT * FROM context_artifacts WHERE id = %s", (artifact_id,)
        ).fetchone()
    return dict(row) if row is not None else None


def _meta_dict(row: dict[str, Any]) -> dict[str, Any]:
    meta = row.get("meta")
    if isinstance(meta, dict):
        return meta
    if isinstance(meta, str) and meta.strip():
        try:
            out = json.loads(meta)
            return out if isinstance(out, dict) else {}
        except (json.JSONDecodeError, ValueError):
            return {}
    return {}


def _resolve_storage_path(raw: str) -> Path | None:
    """Resolve a stored path to a real file: absolute as-is; relative tried against
    the engine root, the repo root, then the CWD. ``None`` when nothing exists."""
    p = Path(raw)
    candidates = [p] if p.is_absolute() else [_ENGINE_ROOT / p, _REPO_ROOT / p, Path.cwd() / p]
    for cand in candidates:
        if cand.is_file():
            return cand
    return None


def _guess_mime(row_mime: str | None, filename: str, path: Path | None = None) -> str | None:
    if row_mime and row_mime.strip():
        return row_mime.strip().lower()
    guessed = mimetypes.guess_type(filename)[0]
    if not guessed and path is not None:
        guessed = mimetypes.guess_type(str(path))[0]
    return guessed


def load_artifact_media(
    artifact_id: str,
    *,
    dsn: str | None = None,
    fetch_row: Callable[..., dict[str, Any] | None] | None = None,
) -> ArtifactMedia:
    """Load ``artifact_id``'s binary content; raise :class:`ArtifactMediaError`
    with a concrete reason on ANY failure (never a silent empty result).

    ``fetch_row`` is the injectable row seam for tests (``(artifact_id, dsn=...) ->
    dict | None``); default reads the real ``context_artifacts`` table."""
    fetch = fetch_row or _fetch_artifact_row
    try:
        row = fetch(artifact_id, dsn=dsn)
    except Exception as exc:  # noqa: BLE001 — surface the real store error, fail closed
        raise ArtifactMediaError(
            f"context_artifacts read failed for {artifact_id!r}: {exc}"
        ) from exc
    if row is None:
        raise ArtifactMediaError(f"artifact {artifact_id!r} not found in context_artifacts")
    if row.get("active") is False:
        raise ArtifactMediaError(f"artifact {artifact_id!r} was removed (active=false)")

    meta = _meta_dict(row)
    filename = (str(row.get("name") or "").strip()) or artifact_id
    row_mime = row.get("media_type")

    # 1) On-disk storage (the studio worktree's var/artifacts/... files) — the real
    #    original bytes. Column preferred, meta key accepted.
    storage_path = row.get("storage_path") or meta.get("storage_path")
    if storage_path:
        path = _resolve_storage_path(str(storage_path))
        if path is None:
            raise ArtifactMediaError(
                f"artifact {artifact_id!r} storage_path {str(storage_path)!r} "
                "does not exist on disk"
            )
        content = path.read_bytes()
        if not content:
            raise ArtifactMediaError(
                f"artifact {artifact_id!r} storage_path {str(storage_path)!r} is empty"
            )
        return ArtifactMedia(
            artifact_id=artifact_id,
            filename=filename,
            mime_type=_guess_mime(row_mime, filename, path),
            content_bytes=content,
            source="storage_path",
        )

    # 2) An explicit base64 content column / meta key (legacy + forward-compat).
    for key in ("content_b64", "content_base64", "content"):
        raw = row.get(key) or meta.get(key)
        if not raw or not isinstance(raw, str):
            continue
        payload = raw.strip()
        m = _DATA_URI_RE.match(payload)
        data_uri_mime = None
        if m:
            data_uri_mime = m.group("mime")
            payload = m.group("b64")
        try:
            content = base64.b64decode(payload, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ArtifactMediaError(
                f"artifact {artifact_id!r} column {key!r} is not valid base64: {exc}"
            ) from exc
        if not content:
            raise ArtifactMediaError(f"artifact {artifact_id!r} column {key!r} decoded empty")
        return ArtifactMedia(
            artifact_id=artifact_id,
            filename=filename,
            mime_type=(data_uri_mime or _guess_mime(row_mime, filename)),
            content_bytes=content,
            source="base64_column",
        )

    # 3) Last resort: the registry's bounded `preview` data-uri (a REAL image of the
    #    artifact, possibly downscaled). Provenance is recorded so the audit is honest
    #    about the bytes being the stored preview, not a disk original.
    preview = row.get("preview")
    if isinstance(preview, str) and preview.strip():
        m = _DATA_URI_RE.match(preview.strip())
        if m is None:
            raise ArtifactMediaError(
                f"artifact {artifact_id!r} preview is not a base64 data-uri "
                "(cannot recover binary content)"
            )
        try:
            content = base64.b64decode(m.group("b64"), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ArtifactMediaError(
                f"artifact {artifact_id!r} preview data-uri is not valid base64: {exc}"
            ) from exc
        if not content:
            raise ArtifactMediaError(f"artifact {artifact_id!r} preview decoded empty")
        return ArtifactMedia(
            artifact_id=artifact_id,
            filename=filename,
            mime_type=(m.group("mime") or _guess_mime(row_mime, filename)),
            content_bytes=content,
            source="preview_data_uri",
        )

    raise ArtifactMediaError(
        f"artifact {artifact_id!r} has no storage_path on disk and no stored "
        "base64 content/preview — its binary content is not recoverable"
    )
