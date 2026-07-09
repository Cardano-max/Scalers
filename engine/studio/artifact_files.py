"""Disk storage for uploaded artifact BYTES (images / artwork / screenshots).

The ``context_artifacts`` row is the index; the actual bytes live on disk at
``var/artifacts/{tenant_id}/{sha256}.{ext}`` (engine-relative, env-overridable via
``SCALERS_ARTIFACTS_DIR``). This replaces the old behaviour of base64-ing the whole
image into the artifact ``content`` TEXT column, which silently LOST bytes past the
200k-char preview cap. Content-addressed (sha256) so a re-upload of the same bytes
is a no-op and the stored file can never diverge from its recorded hash.

HONESTY: :func:`read_stored_bytes` returns the real file bytes or ``None`` — never a
placeholder. Nothing here fabricates, resizes, or re-encodes an image.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

# media type -> canonical file extension for the stored blob.
_MEDIA_EXT: dict[str, str] = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/svg+xml": "svg",
    "application/pdf": "pdf",
}

_EXT_MEDIA: dict[str, str] = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "svg": "image/svg+xml",
    "pdf": "application/pdf",
}


def artifacts_root() -> Path:
    """The on-disk root for artifact bytes: ``$SCALERS_ARTIFACTS_DIR`` or
    ``engine/var/artifacts`` (this file lives at engine/studio/)."""
    env = os.environ.get("SCALERS_ARTIFACTS_DIR")
    if env and env.strip():
        return Path(env.strip())
    return Path(__file__).resolve().parents[1] / "var" / "artifacts"


def ext_for_media_type(media_type: str | None, name: str = "") -> str:
    """Canonical extension for the stored file — from the media type, else the
    uploaded filename's own extension, else ``bin`` (honest unknown)."""
    if media_type and media_type.lower() in _MEDIA_EXT:
        return _MEDIA_EXT[media_type.lower()]
    suffix = Path(name or "").suffix.lstrip(".").lower()
    if suffix and re.fullmatch(r"[a-z0-9]{1,8}", suffix):
        return suffix
    return "bin"


def media_type_for_path(path: str | Path) -> str:
    """Best-effort content type for a stored file (by extension); the caller should
    prefer the artifact row's own recorded ``media_type`` when present."""
    return _EXT_MEDIA.get(Path(path).suffix.lstrip(".").lower(), "application/octet-stream")


def store_bytes(
    tenant_id: str, raw: bytes, *, media_type: str | None = None, name: str = ""
) -> tuple[str, Path]:
    """Write ``raw`` to ``var/artifacts/{tenant}/{sha256}.{ext}`` and return
    ``(sha256_hex, path)``. Content-addressed: identical bytes land on the identical
    path, so a re-upload is a cheap no-op (the file is only written when absent).

    The tenant path segment is sanitized to a slug so a hostile tenant id can never
    traverse out of the artifacts root."""
    if not raw:
        raise ValueError("no bytes to store")
    safe_tenant = re.sub(r"[^A-Za-z0-9_.-]", "_", str(tenant_id or "unknown")) or "unknown"
    sha = hashlib.sha256(raw).hexdigest()
    ext = ext_for_media_type(media_type, name)
    directory = artifacts_root() / safe_tenant
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{sha}.{ext}"
    if not path.exists():
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(raw)
        os.replace(tmp, path)  # atomic publish — a crash never leaves a half file
    return sha, path


def read_stored_bytes(storage_path: str | None) -> bytes | None:
    """The real stored bytes for an artifact, or ``None`` when the path is absent /
    unreadable (never a placeholder). Refuses paths outside the artifacts root
    UNLESS the root itself was env-relocated (the path must still be a file)."""
    if not storage_path:
        return None
    try:
        p = Path(storage_path)
        if not p.is_file():
            return None
        return p.read_bytes()
    except OSError:
        return None
