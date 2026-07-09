"""Shared RFC822 message building + attachment validation for the mail connectors.

One implementation, two transports: the Gmail REST connector
(:mod:`connectors.gmail`) base64url-encodes the built message into the API's
``raw`` field; the SMTP app-password fallback (:mod:`connectors.smtp_mail`)
hands the same message to ``smtplib``. Keeping the build + validation here means
an attachment behaves identically on both paths — same allowed types, same size
cap, same fail-closed refusal.

FAIL-CLOSED CONTRACT (spec §10/§13): a draft that promised an attachment must
either send WITH that attachment or fail with a concrete reason. Validation
errors raise :class:`MailAttachmentError` BEFORE any network call — sending
without the promised attachment is never ok, and an attachment is never
silently dropped.

Every attachment that passes validation yields an :class:`AttachmentReceipt`
(filename + sha256 + size + mime) so the send audit can record exactly what
was attached — content bytes are hashed, never logged.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any

#: MIME types an outbound mail attachment may carry (spec §10): images the studio
#: produces (png/jpeg/webp) + PDF. Anything else is refused with a concrete error.
ALLOWED_ATTACHMENT_MIME_TYPES: frozenset[str] = frozenset(
    {"image/png", "image/jpeg", "image/webp", "application/pdf"}
)

#: Hard cap on the TOTAL attachment payload of one message (Gmail rejects ~25MB
#: whole-message; we stop earlier with our own honest error).
MAX_ATTACHMENT_TOTAL_BYTES: int = 20 * 1024 * 1024  # 20 MB across ALL attachments

# Common aliases normalized to their canonical MIME type before the allowlist
# check (an operator-uploaded 'image/jpg' is a jpeg, not a policy violation).
_MIME_ALIASES: dict[str, str] = {"image/jpg": "image/jpeg", "image/pjpeg": "image/jpeg"}


class MailAttachmentError(ValueError):
    """An attachment failed validation (bad shape / disallowed type / over the
    size cap). FAIL CLOSED: the caller must NOT send the message without the
    promised attachment — surface this error instead. Never carries content
    bytes, only filename/type/size facts."""


@dataclass(frozen=True)
class AttachmentReceipt:
    """The audit-safe record of one validated attachment (no content bytes)."""

    filename: str
    mime_type: str
    size_bytes: int
    sha256: str  # full hex digest; audit surfaces show :attr:`sha256_prefix`

    @property
    def sha256_prefix(self) -> str:
        return self.sha256[:12]

    def audit_label(self) -> str:
        """One line for the send audit: filename + sha256 prefix + size + type."""
        return (
            f"{self.filename} (sha256:{self.sha256_prefix}, "
            f"{self.size_bytes}B, {self.mime_type})"
        )


def validate_attachments(
    attachments: Sequence[Mapping[str, Any]] | None,
) -> tuple[list[dict[str, Any]], tuple[AttachmentReceipt, ...]]:
    """Validate + normalize ``attachments`` (list of ``{filename, content_bytes,
    mime_type}``); return ``(normalized, receipts)``.

    Raises :class:`MailAttachmentError` with a CONCRETE reason on any problem —
    missing/empty filename or bytes, a MIME type outside
    :data:`ALLOWED_ATTACHMENT_MIME_TYPES`, or a total payload over
    :data:`MAX_ATTACHMENT_TOTAL_BYTES`. ``None``/empty input is a clean no-op
    (``([], ())``) — no attachment was promised, nothing to fail on."""
    if not attachments:
        return [], ()
    normalized: list[dict[str, Any]] = []
    receipts: list[AttachmentReceipt] = []
    total = 0
    for i, att in enumerate(attachments):
        if not isinstance(att, Mapping):
            raise MailAttachmentError(
                f"attachment #{i}: expected a mapping with filename/content_bytes/"
                f"mime_type, got {type(att).__name__}"
            )
        filename = att.get("filename")
        if not isinstance(filename, str) or not filename.strip():
            raise MailAttachmentError(f"attachment #{i}: missing or empty filename")
        filename = filename.strip()

        content = att.get("content_bytes")
        if isinstance(content, bytearray):
            content = bytes(content)
        if not isinstance(content, bytes) or len(content) == 0:
            raise MailAttachmentError(
                f"attachment {filename!r}: content_bytes must be non-empty bytes"
            )

        mime = att.get("mime_type")
        if not isinstance(mime, str) or not mime.strip():
            raise MailAttachmentError(
                f"attachment {filename!r}: missing mime_type "
                f"(allowed: {', '.join(sorted(ALLOWED_ATTACHMENT_MIME_TYPES))})"
            )
        mime = mime.strip().lower()
        mime = _MIME_ALIASES.get(mime, mime)
        if mime not in ALLOWED_ATTACHMENT_MIME_TYPES:
            raise MailAttachmentError(
                f"attachment {filename!r}: mime type {mime!r} not allowed "
                f"(allowed: {', '.join(sorted(ALLOWED_ATTACHMENT_MIME_TYPES))})"
            )

        total += len(content)
        if total > MAX_ATTACHMENT_TOTAL_BYTES:
            raise MailAttachmentError(
                f"attachments exceed the {MAX_ATTACHMENT_TOTAL_BYTES // (1024 * 1024)}MB "
                f"total cap ({total} bytes reached at {filename!r})"
            )

        normalized.append(
            {"filename": filename, "content_bytes": content, "mime_type": mime}
        )
        receipts.append(
            AttachmentReceipt(
                filename=filename,
                mime_type=mime,
                size_bytes=len(content),
                sha256=hashlib.sha256(content).hexdigest(),
            )
        )
    return normalized, tuple(receipts)


def build_mail_message(
    *,
    to: str,
    subject: str,
    body: str,
    from_addr: str | None = None,
    attachments: Sequence[Mapping[str, Any]] | None = None,
) -> tuple[EmailMessage, tuple[AttachmentReceipt, ...]]:
    """Build the outbound :class:`EmailMessage` (validating attachments first);
    return ``(message, receipts)``.

    With attachments the message becomes multipart/mixed via
    ``EmailMessage.add_attachment``; without, it stays the plain single-part
    message the existing send path produced (byte-compatible back-compat)."""
    normalized, receipts = validate_attachments(attachments)
    msg = EmailMessage()
    msg["To"] = to
    if from_addr:
        msg["From"] = from_addr
    msg["Subject"] = subject
    msg.set_content(body)
    for att in normalized:
        maintype, _, subtype = att["mime_type"].partition("/")
        msg.add_attachment(
            att["content_bytes"],
            maintype=maintype,
            subtype=subtype,
            filename=att["filename"],
        )
    return msg, receipts
