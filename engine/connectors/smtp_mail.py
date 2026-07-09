"""SMTP app-password mail connector (Gmail-API fallback, "Option B") — gated, secret-safe.

The Gmail REST connector's OAuth refresh token can die (``invalid_grant`` —
expired/revoked, needs operator re-consent). The operator chose an SMTP
app-password fallback: SMTP over SSL to ``smtp.gmail.com:465`` authenticated with
``SMTP_SENDER`` (the sending address) + ``SMTP_APP_PASSWORD`` (a Google app
password). This connector implements ONLY the transport; every send-path gate
lives in :mod:`actions.publish` and applies IDENTICALLY to both transports
(tenant TEST-MODE registry, ``GMAIL_REDIRECT_TO`` redirect, unresolved-placeholder
refusal, exactly-once claim) because the fallback is selected INSIDE
``_publish_gmail`` after all of them have run.

Contract (mirrors :class:`connectors.gmail.GmailConnector`):

* **disabled by default** (``enabled=False`` → :class:`ConnectorDisabledError`);
* credentials are read **key-from-env** (``SMTP_SENDER`` / ``SMTP_APP_PASSWORD``)
  — never hardcoded, NEVER logged/echoed (``__repr__`` redacts; error text is
  scrubbed defensively);
* missing creds → :class:`SmtpConfigError` (fail closed, never a silent no-op);
* attachments share :mod:`connectors.mail_message` (same allowed types / 20MB
  cap / fail-closed refusal — one validator, two transports);
* a transport failure raises :class:`SmtpSendError` carrying the REAL smtplib
  error — never a fake success.

The SMTP client is injected via ``smtp_factory`` so tests drive the exact
login/send calls without any network; the default builds
``smtplib.SMTP_SSL(smtp.gmail.com, 465)`` with a verified TLS context.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import make_msgid
from typing import Any

from connectors.base import GatedConnector, redact
from connectors.mail_message import AttachmentReceipt, build_mail_message

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465  # implicit SSL (SMTPS)

# Env keys the connector reads (key-from-env; never a vendored .env, never logged).
ENV_SMTP_SENDER = "SMTP_SENDER"
ENV_SMTP_APP_PASSWORD = "SMTP_APP_PASSWORD"


class SmtpConfigError(RuntimeError):
    """A send was attempted without SMTP_SENDER/SMTP_APP_PASSWORD (key-from-env
    required). Fail closed; never echoes a credential value."""


class SmtpSendError(RuntimeError):
    """The SMTP login/submission failed. Carries the real smtplib error (scrubbed
    of the app password defensively); NEVER raised as a fake success."""


@dataclass(frozen=True)
class SmtpSendResult:
    """The result of a real SMTP submission.

    SMTP has no provider-side message id at submission time, so ``message_id`` is
    the RFC822 ``Message-ID`` header we generated and handed to the server — the
    durable identifier the sent message carries. ``deep_link`` is ``None`` (no
    console link for raw SMTP)."""

    message_id: str
    deep_link: str | None = None
    to: str | None = None
    subject: str | None = None
    attachments: tuple[AttachmentReceipt, ...] = ()
    transport: str = "smtp"


def _default_smtp_factory(host: str, port: int, timeout: float):
    """Build the real ``smtplib.SMTP_SSL`` client (verified TLS, stdlib only)."""
    import smtplib
    import ssl

    return smtplib.SMTP_SSL(host, port, timeout=timeout, context=ssl.create_default_context())


class SmtpMailConnector(GatedConnector):
    """SMTP-over-SSL mail connector — disabled by default, key-from-env creds.

    Same public ``send`` contract as :class:`connectors.gmail.GmailConnector`
    (to/subject/body/from_addr/attachments) so :mod:`actions.publish` can swap
    transports without changing any safety behavior.
    """

    name = "smtp"
    provider_name = "gmail-smtp"

    def __init__(
        self,
        *,
        sender: str | None = None,
        app_password: str | None = None,
        smtp_factory: Callable[[str, int, float], Any] | None = None,
        enabled: bool = False,
        timeout: float = 15.0,
        host: str = SMTP_HOST,
        port: int = SMTP_PORT,
    ) -> None:
        super().__init__(enabled=enabled, timeout=timeout)
        self._sender = sender
        self._app_password = app_password
        self._smtp_factory = smtp_factory or _default_smtp_factory
        self._host = host
        self._port = port

    @classmethod
    def from_env(
        cls,
        *,
        enabled: bool = False,
        env: Mapping[str, str] | None = None,
        smtp_factory: Callable[[str, int, float], Any] | None = None,
        timeout: float = 15.0,
    ) -> "SmtpMailConnector":
        """Build a connector reading creds from ``env`` (defaults to ``os.environ``)."""
        import os

        e = env if env is not None else os.environ
        return cls(
            sender=e.get(ENV_SMTP_SENDER),
            app_password=e.get(ENV_SMTP_APP_PASSWORD),
            smtp_factory=smtp_factory,
            enabled=enabled,
            timeout=timeout,
        )

    @classmethod
    def configured_in_env(cls, env: Mapping[str, str] | None = None) -> bool:
        """True iff BOTH SMTP_SENDER and SMTP_APP_PASSWORD are set (non-empty).
        The publish path uses this to decide whether a fallback even exists —
        values are only checked for presence, never read into a log."""
        import os

        e = env if env is not None else os.environ
        return bool((e.get(ENV_SMTP_SENDER) or "").strip()) and bool(
            (e.get(ENV_SMTP_APP_PASSWORD) or "").strip()
        )

    def __repr__(self) -> str:  # never leak the app password in a repr/log
        return (
            f"SmtpMailConnector(enabled={self._enabled}, host={self._host!r}, "
            f"sender={self._sender!r}, app_password={redact(self._app_password)})"
        )

    def _scrub(self, text: str) -> str:
        """Defensively remove the app password from any error text (smtplib errors
        do not normally echo credentials, but no error path may ever leak one)."""
        if self._app_password and self._app_password in text:
            text = text.replace(self._app_password, redact(self._app_password))
        return text

    # ── the real send path ────────────────────────────────────────────────────

    def send(
        self,
        to: str,
        subject: str,
        body: str,
        *,
        from_addr: str | None = None,
        attachments: Sequence[Mapping[str, Any]] | None = None,
    ) -> SmtpSendResult:
        """Send a real email via SMTP-over-SSL. Disabled-by-default; missing creds
        refuse (:class:`SmtpConfigError`); attachments validate FAIL-CLOSED before
        any connection (shared :mod:`connectors.mail_message`); a transport error
        raises :class:`SmtpSendError` — never a fake success."""
        self._require_enabled()
        if not (self._sender and self._app_password):
            raise SmtpConfigError(
                "smtp connector missing SMTP_SENDER/SMTP_APP_PASSWORD "
                "(key-from-env required)"
            )
        # Build (and thereby validate) the message BEFORE any connection: an
        # invalid attachment refuses the send without touching the network.
        msg, receipts = build_mail_message(
            to=to,
            subject=subject,
            body=body,
            from_addr=from_addr or self._sender,
            attachments=attachments,
        )
        message_id = make_msgid()
        msg["Message-ID"] = message_id
        try:
            client = self._smtp_factory(self._host, self._port, self._timeout)
            try:
                client.login(self._sender, self._app_password)
                self._send_message(client, msg)
            finally:
                try:
                    client.quit()
                except Exception:  # noqa: BLE001 — best-effort close, send outcome already known
                    pass
        except Exception as exc:  # noqa: BLE001 — surface the REAL error, scrubbed, never fake success
            raise SmtpSendError(f"smtp send failed: {self._scrub(str(exc))}") from exc
        return SmtpSendResult(
            message_id=message_id,
            deep_link=None,
            to=to,
            subject=subject,
            attachments=receipts,
        )

    @staticmethod
    def _send_message(client: Any, msg: EmailMessage) -> None:
        """Submit via ``send_message``; a non-empty refused-recipients dict is a
        FAILURE (partial acceptance must never read as a clean success)."""
        refused = client.send_message(msg)
        if refused:
            raise RuntimeError(f"smtp server refused recipient(s): {sorted(refused)}")
