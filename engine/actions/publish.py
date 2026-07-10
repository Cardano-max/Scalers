"""Approve → publish: send an operator-approved action via the real connector.

This is the human-authorized side-effect path of the live demo. The console's
``approveAction`` mutation calls :func:`approve_and_publish`; ``rejectAction``
calls :func:`reject`. Connector selection is by the action's ``channel``:

* ``gmail``     → :class:`connectors.gmail.GmailConnector` (enabled, real creds
  from env) → a **real** ``users.messages.send`` → ``status='sent'`` + deep_link.
* ``facebook`` / ``instagram`` → CREDENTIAL-GATED (social ready queue). The
  operator has not yet provided verified Meta credentials, so the live path
  checks them FIRST — ``META_PAGE_TOKEN`` + ``META_IG_USER_ID`` for instagram,
  ``META_PAGE_TOKEN`` + ``META_PAGE_ID`` for facebook — and REFUSES fail-closed
  (:class:`MetaCredentialsMissingError`, BEFORE the exactly-once claim, so the
  draft STAYS PENDING in the ready queue with the reason on ``last_error``).
  With credentials present the post routes to the :func:`publish_to_meta` seam,
  which deliberately raises ``NotImplementedError`` until the operator's
  credentials are verified — **never a fake publish, never a silent drop**.
  An injected test connector bypasses the env gate exactly like every other
  channel's test seam (``connectors={"instagram": fake}``); an IG comment reply
  with a real connector still raises the REAL Graph error on failure.
  (An IG post additionally needs a PUBLIC image URL — per-action media resolved
  from ``context.artwork``/``context.attachment_artifact_id`` (+ optional
  ``PUBLIC_ASSET_BASE_URL``), with the global ``DEMO_IG_IMAGE_URL`` as a
  logged last-resort fallback — and, for a non-test user, Meta App Review of
  ``instagram_content_publish``.)

**Exactly-once.** A real external send (a Gmail message) is not transactional, so
the guarantee is the durable status: if the action is already ``sent`` we return
it without re-sending. The ``idempotency_key`` is UNIQUE on the row, so the
logical action exists once; the ``sent`` short-circuit is the publish-side guard.
(A crash strictly between the provider accepting the send and our ``sent`` commit
is the irreducible two-generals window — surfaced, never silently double-sent in
the same process.)

For testability the connectors can be injected (``connectors={"gmail": fake}``);
left to the default, real env-backed connectors are built — so the live path is
genuinely live and the tests never touch the network.

**Real-only (Slice-5).** The live path selects a connector purely by channel and
builds a real env-backed connector when none is injected — no mock is wired in. As
defense-in-depth, :func:`_ensure_real` refuses to send through any connector that
declares ``is_mock = True`` (e.g. :class:`sideeffects.posting.MockPostingConnector`)
so a mock can never perform a real IG/Gmail/FB send: it fails honestly instead.
"""

from __future__ import annotations

import inspect
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

from actions.store import ActionRow, _connect, get_action, update_status

_log = logging.getLogger(__name__)

# A draft must never deliver a raw template placeholder (e.g. the copywriter's
# ``{{unsubscribe}}`` token) to a real recipient. The studio draft builder resolves
# these before staging; this is the send-path backstop that REFUSES to send if one
# survived. Honest failure, never a raw token in someone's inbox.
_PLACEHOLDER_RE = re.compile(r"\{\{[^}]*\}\}")


class ActionNotFoundError(LookupError):
    """``approve_and_publish``/``reject`` was given an unknown action id."""


class TestModeSendBlockedError(RuntimeError):
    """A send was refused by the SERVER-SIDE tenant TEST-MODE gate (ju1.1).

    Raised BEFORE the exactly-once claim and BEFORE any connector is built, so the
    action stays PENDING (re-approvable once the tenant is un-held) and no side
    effect can occur — regardless of redirect config or the operator live toggle."""


class MetaCredentialsMissingError(RuntimeError):
    """An instagram/facebook publish was refused because the operator's Meta
    credentials are not configured (social ready queue, fail-closed).

    Raised BEFORE the exactly-once claim and BEFORE any connector is built —
    exactly like :class:`TestModeSendBlockedError` — so the action stays PENDING
    (waiting in the ready queue, re-approvable the moment credentials arrive)
    and no side effect can occur. Callers that batch (campaign send, scheduler)
    treat it as blocked/skipped, never a silent failure."""


class MockOnLivePathError(RuntimeError):
    """A mock/stub connector was about to perform a REAL send on the live
    approve→publish path. Refused: a live action MUST use a real connector
    (mocks are test-only). The action is marked ``failed`` with this error —
    never a fake/silent success."""


#: Channels that execute in a local SANDBOX with no external provider (tlv.6 demo
#: slice). They route to :func:`_publish_demo` and are exempt from the real-send
#: test-mode gate because they cannot reach anyone — see the gate comment in
#: :func:`approve_and_publish`.
SANDBOX_CHANNELS = frozenset({"demo"})

#: The OPERATOR-PROVIDED Meta credential env keys (social ready queue contract).
#: These are the canonical names the operator will set when Meta access is
#: verified; until every key a channel needs is present, its publishes refuse
#: fail-closed and the drafts wait in the ready queue.
META_ENV_PAGE_TOKEN = "META_PAGE_TOKEN"
META_ENV_IG_USER_ID = "META_IG_USER_ID"
META_ENV_PAGE_ID = "META_PAGE_ID"

#: Env keys each Meta channel requires before its publish gate opens.
_META_REQUIRED_ENV: dict[str, tuple[str, ...]] = {
    "instagram": (META_ENV_PAGE_TOKEN, META_ENV_IG_USER_ID),
    "facebook": (META_ENV_PAGE_TOKEN, META_ENV_PAGE_ID),
}

#: Channel-name aliases seen on REAL rows (the campaign planner writes 'ig'):
#: folded once here so the credential gate, the channel dispatch, and the social
#: ready queue all read the same vocabulary — an 'ig' draft must hit the
#: instagram gate, never fall through to "unknown channel".
CHANNEL_ALIASES: dict[str, str] = {"ig": "instagram", "fb": "facebook"}


def normalize_channel(channel: str | None) -> str:
    """Lower-cased channel with production aliases folded ('ig' → 'instagram')."""
    lowered = (channel or "").lower()
    return CHANNEL_ALIASES.get(lowered, lowered)


def meta_credentials_blocked_reason(channel: str) -> str | None:
    """``None`` when the operator's Meta credentials for ``channel`` are all set
    (non-blank) in the env; otherwise the honest refusal reason naming the exact
    keys the channel needs. Non-Meta channels are never blocked here (``None``).

    Single source of truth for the publish gate AND the social ready queue's
    ``publishable``/``blocked_reason`` fields — the queue shows exactly the
    reason an approve would refuse with."""
    required = _META_REQUIRED_ENV.get(normalize_channel(channel))
    if not required:
        return None
    if all(os.environ.get(key, "").strip() for key in required):
        return None
    return f"Meta credentials not configured ({' / '.join(required)})"


def _sandbox_delivery_tenants() -> frozenset[str]:
    """Tenants whose approved actions execute on the sandbox channel instead of any
    real provider (tlv.6 demo). Read from ``SANDBOX_DELIVERY_TENANTS`` (comma-sep);
    EMPTY by default so no real tenant is ever redirected without explicit opt-in."""
    raw = os.environ.get("SANDBOX_DELIVERY_TENANTS", "")
    return frozenset(t.strip() for t in raw.split(",") if t.strip())


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _with_mode(row: ActionRow | None, mode: str) -> ActionRow | None:
    """Attach the send ``mode`` ('live' | 'test_redirect') to a returned action row.

    ``mode`` is a send-time computed value, not a persisted ``actions`` column, so it
    rides back as a transient attribute on the returned :class:`ActionRow` for the
    caller / UI to read (the Live-Feed shows a 'Live' vs 'Test/redirect' badge). It is
    absent (``getattr(row, 'mode', None)`` -> ``None``) on any freshly loaded row that
    did not go through a send."""
    if row is not None:
        row.mode = mode
    return row


def _action_context(action: ActionRow) -> dict[str, Any]:
    """The action's ``context`` JSON as a dict — DEFENSIVE: ``context`` is a nullable
    TEXT column written by other paths (the studio worktree adds
    ``attachment_artifact_id`` / ``artwork`` for drafts with artwork). Absent /
    unparseable / non-object context degrades to ``{}`` — a graceful no-op, never a
    crash on a legacy row."""
    raw = getattr(action, "context", None)
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        out = json.loads(raw)
        return out if isinstance(out, dict) else {}
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}


def _context_attachment_artifact_id(ctx: dict[str, Any]) -> str | None:
    """The artifact id the draft PROMISED as its attachment/media, or ``None``.

    Reads ``context.attachment_artifact_id`` (snake_case and camelCase) first, then
    ``context.artwork.artifactId``/``artifact_id`` (the studio's artwork block).
    ``None`` = the draft never promised media → the send proceeds without any."""
    for key in ("attachment_artifact_id", "attachmentArtifactId"):
        v = ctx.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    art = ctx.get("artwork")
    if isinstance(art, dict):
        for key in ("artifactId", "artifact_id"):
            v = art.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return None


def _record_send_audit_row(
    action: ActionRow,
    *,
    mode: str | None,
    result: str | None,
    transport: str,
    provider_id: str | None = None,
    attachments: tuple[Any, ...] = (),
    detail: str | None = None,
    dsn: str | None = None,
) -> None:
    """ONE consistent ``send_audit`` row per publish attempt (every channel/transport).

    ``kind='send'`` (additive next to the campaign-level 'send_eligible'/'override'
    rows); ``mode`` = 'live' | 'test_redirect' | 'sandbox' | 'blocked'; ``reason``
    carries a compact JSON note: transport ('gmail-api' | 'gmail-smtp-fallback' |
    'sandbox' | 'instagram-graph' | 'facebook-graph'), the provider id, and any
    attachment receipts (filename + sha256 prefix — NEVER content). Best-effort:
    auditing must never break (or double-fire) the send itself."""
    try:
        from actions.audit import record_send_audit

        note: dict[str, Any] = {"transport": transport}
        if provider_id:
            note["provider_id"] = provider_id
        if attachments:
            note["attachments"] = [
                r.audit_label() if hasattr(r, "audit_label") else str(r) for r in attachments
            ]
        if detail:
            note["detail"] = detail[:500]
        record_send_audit(
            action_id=action.id,
            kind="send",
            run_id=getattr(action, "run_id", None),
            tenant_id=getattr(action, "tenant_id", None),
            reason=json.dumps(note, ensure_ascii=False),
            conf=getattr(action, "conf", None),
            threshold=getattr(action, "threshold", None),
            esc_kind=getattr(action, "esc_kind", None),
            result=result,
            mode=mode,
            dsn=dsn,
        )
    except Exception:  # noqa: BLE001 — the audit row is best-effort, the send record is the DB row
        _log.debug("send_audit row write failed (non-fatal)", exc_info=True)


def _ensure_real(conn: Any) -> None:
    """Defense-in-depth real-only guard for the live send path (Slice-5).

    ``approve_and_publish`` already builds real env-backed connectors by
    construction (no mock is wired into the live path), but this makes ``real-only``
    *enforced at runtime* rather than merely conventional: a connector that declares
    ``is_mock = True`` (e.g. :class:`sideeffects.posting.MockPostingConnector`) can
    NEVER perform a real IG/Gmail/FB send here. Real connectors and the unit-test
    fakes do not set ``is_mock``, so the test seam is unaffected — only an actual
    mock is refused, and the action fails honestly instead of faking a send."""
    if getattr(conn, "is_mock", False):
        raise MockOnLivePathError(
            f"refusing to publish via a mock connector ({type(conn).__name__}) on the "
            "live path; a live send requires a real connector (mocks are test-only)"
        )


def claim_for_send(action_id: str, *, dsn: str | None = None) -> ActionRow | None:
    """Atomically claim a PENDING action for sending (exactly-once guard).

    A single conditional UPDATE flips ``pending`` → ``sending`` and stamps
    ``approved_at`` (operator authorization) in one round-trip::

        UPDATE actions SET status='sending', autonomy='approved',
               approved_at=now(), updated_at=now()
        WHERE id=%s AND status='pending'
        RETURNING *

    Returns the claimed row (now ``sending``) iff THIS call won the race; returns
    ``None`` when 0 rows matched — the action was already claimed/sent/terminal by
    a concurrent or retried approve, so the caller must NOT send again. This is the
    real exactly-once seam: the old code wrote ``approved`` *before* the send, so a
    retry/second-approve after a crash re-entered and SENT AGAIN (Gmail has no
    provider idempotency). The atomic claim makes the pending→sending transition
    the single serialization point — only one approve can proceed to the send."""
    with _connect(dsn) as conn:
        row = conn.execute(
            "UPDATE actions SET status='sending', autonomy='approved', "
            "approved_at=now(), updated_at=now() "
            "WHERE id=%s AND status='pending' RETURNING *",
            (action_id,),
        ).fetchone()
    return ActionRow.from_row(row) if row is not None else None


def approve_and_publish(
    action_id: str,
    *,
    connectors: dict[str, Any] | None = None,
    dsn: str | None = None,
    live: bool = False,
) -> ActionRow:
    """Approve an action and publish it through the channel's real connector.

    Returns the updated :class:`ActionRow`. Idempotent on a sent action (returns
    it unchanged, no second send). A connector failure marks the action ``failed``
    with the real error — it is never reported as a success.

    ``live`` is the operator's EXPLICIT live-send authorization (default ``False`` —
    the safe redirect default). When ``True`` the just-claimed action is marked with
    the allow-listed live worker (``studio_real_send``) so the gmail send bypasses the
    ``GMAIL_REDIRECT_TO`` safety redirect and reaches the real recipient with a CLEAN
    subject (no ``[TEST]`` marker). This is the ONLY place ``studio_real_send`` is set,
    and it is set only on the row this call now exclusively owns (the claim won), so it
    never races the claim. Without ``live`` the worker is untouched and the safe
    redirect default stands. ``live`` is inert for non-gmail channels.
    """
    connectors = connectors or {}
    action = get_action(action_id, dsn=dsn)
    if action is None:
        raise ActionNotFoundError(action_id)

    # Exactly-once: a sent action is terminal — return it, do not re-send.
    if action.status == "sent":
        return action

    channel = normalize_channel(action.channel)

    # tlv.6 SANDBOX DELIVERY REDIRECT: a designated demo tenant's approved actions
    # execute on the credential-free sandbox channel instead of any real provider, so
    # the demo's approve -> deliver loop closes end-to-end with zero external auth. The
    # draft keeps its real channel/copy; only the EXECUTION is sinked (labeled
    # 'Delivered (sandbox)'). Fail-safe: only tenants explicitly listed in
    # SANDBOX_DELIVERY_TENANTS redirect (empty by default), and a redirect can ONLY
    # make a send safer — the sandbox never reaches a provider.
    if channel not in SANDBOX_CHANNELS and action.tenant_id in _sandbox_delivery_tenants():
        channel = "demo"

    # SERVER-SIDE TEST-MODE GATE (ju1.1) — the hard sandbox for tenants holding real
    # client PII (skindesign): if the tenant is in test_mode, refuse EVERY send whose
    # recipient is not on the operator-approved allowlist, BEFORE the claim and BEFORE
    # any connector exists. Deliberately above ``live=``/redirect handling: no toggle
    # or env config can reach past this. Unknown tenants (no registry row) pass
    # through unchanged (ladies8391 behavior identical).
    #
    # SANDBOX channels (tlv.6 demo) are EXEMPT — and this is NOT a weakening: a
    # sandbox channel routes to ``_publish_demo``, which builds no connector and
    # performs no external send, so there is literally nothing for a real-send gate
    # to protect. The gate stays fully in force for every REAL channel — proven by
    # test_publish_demo_channel (gmail to a blocked recipient still raises while the
    # demo channel to the same recipient delivers to the sandbox).
    if channel not in SANDBOX_CHANNELS:
        from tenants.store import check_send_allowed

        allowed, reason = check_send_allowed(action.tenant_id, action.target, dsn=dsn)
        if not allowed:
            update_status(action.id, action.status, dsn=dsn, last_error=reason)
            raise TestModeSendBlockedError(reason)

    # META CREDENTIAL GATE (social ready queue, fail-closed): an instagram/facebook
    # publish on the REAL path (no injected test connector) refuses BEFORE the
    # exactly-once claim when the operator's Meta credentials are absent, so the
    # draft STAYS PENDING — visible and complete in the ready queue, re-approvable
    # the moment credentials arrive — with the honest reason on last_error. Same
    # machinery as the TEST-MODE gate above: no claim, no connector, no side effect.
    if channel in _META_REQUIRED_ENV and connectors.get(channel) is None:
        cred_reason = meta_credentials_blocked_reason(channel)
        if cred_reason is not None:
            update_status(action.id, action.status, dsn=dsn, last_error=cred_reason)
            raise MetaCredentialsMissingError(cred_reason)

    # Exactly-once CLAIM (atomic). Replace the old pre-send 'approved' write with a
    # single conditional UPDATE pending->'sending'. If 0 rows are claimed the action
    # was already taken (concurrent/retried approve, or a crash-window 'sending'/
    # terminal row) — RETURN the current row WITHOUT sending. Only the winning claim
    # proceeds to the external call, so the non-idempotent Gmail send fires once.
    claimed = claim_for_send(action_id, dsn=dsn)
    if claimed is None:
        return get_action(action_id, dsn=dsn)
    action = claimed

    # Operator EXPLICIT live-approval marks the just-claimed action as the allow-listed
    # live worker. Done AFTER the claim won, on the row we now exclusively own, so it
    # never races the atomic claim; it is the single set-site for 'studio_real_send'.
    # With the mark in place the gmail allow-list (worker != 'studio_real_send') sends
    # clean to the real recipient; without ``live`` the redirect default is preserved.
    if live and getattr(action, "worker", None) != "studio_real_send":
        marked = update_status(action.id, action.status, dsn=dsn, worker="studio_real_send")
        if marked is not None:
            action = marked

    atype = (action.type or "").lower()
    # Email is delivered via the Gmail connector. Studio drafts may carry the channel
    # as "email" (and the studio-research path normalises it to "gmail"), but a draft
    # approved straight from the queue can still arrive as "email" — both route to the
    # same real Gmail send. The .lower() above already folds Email/EMAIL/Gmail.
    if channel in ("gmail", "email"):
        return _publish_gmail(
            action, connectors.get("gmail"), dsn, smtp_connector=connectors.get("smtp")
        )
    if channel == "facebook":
        if atype == "comment":
            return update_status(
                action.id, "failed", dsn=dsn,
                last_error="fb comment reply not implemented (post /{comment_id}/comments) — pending",
            )
        return _publish_facebook(action, connectors.get("facebook"), dsn)
    if channel == "instagram":
        if atype == "comment":
            return _reply_instagram(action, connectors.get("instagram"), dsn)
        return _publish_instagram(action, connectors.get("instagram"), dsn)
    if channel in SANDBOX_CHANNELS:
        return _publish_demo(action, connectors.get("demo"), dsn)
    return update_status(
        action.id, "failed", dsn=dsn, last_error=f"unknown channel {action.channel!r}"
    )


def reject(action_id: str, *, dsn: str | None = None) -> ActionRow:
    """Reject an action (operator declined). No send happens."""
    action = get_action(action_id, dsn=dsn)
    if action is None:
        raise ActionNotFoundError(action_id)
    return update_status(action_id, "rejected", dsn=dsn)


# ── per-channel publish ───────────────────────────────────────────────────────


def _gmail_auth_dead(exc: Exception) -> bool:
    """True iff the Gmail REST path failed in a way the SMTP app-password fallback
    (Option B) may legitimately cover: the OAuth refresh token is dead
    (``invalid_grant`` — expired/revoked, needs operator re-consent) or the API
    creds are absent altogether. A provider SEND error (4xx/5xx on
    ``messages.send``) is NOT fallback-eligible — retrying a rejected message on a
    second transport could double-deliver or mask a real policy refusal."""
    from connectors.gmail import GmailAuthError

    if not isinstance(exc, GmailAuthError):
        return False
    msg = str(exc).lower()
    return "invalid_grant" in msg or "missing client_id" in msg


def _publish_gmail_via_smtp(
    action: ActionRow,
    smtp_connector: Any | None,
    dsn: str | None,
    *,
    to_addr: str,
    subject: str,
    body: str,
    send_kwargs: dict[str, Any],
    attachment_receipts: tuple[Any, ...],
    mode: str,
    primary_error: str,
) -> ActionRow | None:
    """The SMTP app-password fallback leg of the gmail send (Option B).

    Called ONLY after every send gate has already run (TEST-MODE tenant gate,
    exactly-once claim, redirect/live resolution, placeholder + attachment
    guards) — ``to_addr``/``subject`` are the ALREADY-GATED values, so the
    fallback can never widen delivery beyond what the Gmail API leg was about
    to do. Returns the final row when the fallback RAN (sent or failed), or
    ``None`` when no SMTP fallback is configured — the caller then surfaces the
    primary Gmail error unchanged (the existing concrete failure, never silent)."""
    from connectors.smtp_mail import SmtpMailConnector

    conn = smtp_connector
    if conn is None:
        if not SmtpMailConnector.configured_in_env():
            return None
        conn = SmtpMailConnector.from_env(enabled=True)
    _log.warning(
        "gmail publish: action=%s gmail api unavailable (%s) — using SMTP "
        "app-password fallback (transport=gmail-smtp-fallback, mode=%s)",
        action.id, primary_error, mode,
    )
    try:
        _ensure_real(conn)  # real-only: a mock never live-sends (same guard, both legs)
        result = conn.send(to=to_addr, subject=subject, body=body, **send_kwargs)
    except Exception as exc:  # noqa: BLE001 — surface BOTH real errors, never fake success
        last_error = f"gmail api: {primary_error}; smtp fallback: {exc}"
        _record_send_audit_row(
            action, mode=mode, result="failed", transport="gmail-smtp-fallback",
            attachments=attachment_receipts, detail=last_error, dsn=dsn,
        )
        return _with_mode(
            update_status(action.id, "failed", dsn=dsn, last_error=last_error), mode
        )
    _record_send_audit_row(
        action, mode=mode, result="sent", transport="gmail-smtp-fallback",
        provider_id=getattr(result, "message_id", None),
        attachments=getattr(result, "attachments", None) or attachment_receipts,
        detail=f"gmail api unavailable: {primary_error}",
        dsn=dsn,
    )
    row = update_status(
        action.id, "sent", dsn=dsn,
        deep_link=getattr(result, "deep_link", None),
        sent_at=_now(),
        outcome_label="Sent (SMTP fallback)",  # honest: not the Gmail API transport
        outcome_kind="success",
    )
    if row is not None:
        row.attachment_receipts = attachment_receipts
        row.transport = "gmail-smtp-fallback"
    return _with_mode(row, mode)


def _publish_gmail(
    action: ActionRow,
    connector: Any | None,
    dsn: str | None,
    *,
    smtp_connector: Any | None = None,
) -> ActionRow:
    from connectors.gmail import GmailConnector

    conn = connector or GmailConnector.from_env(enabled=True)

    # SEND MODE — explicit and honest (computed BEFORE any return so every returned
    # row carries it):
    #   * live          — a REAL send to the REAL recipient with a CLEAN subject. Either
    #                     the action was explicitly staged for real outreach (worker
    #                     'studio_real_send' = an operator live-approval), or no test
    #                     redirect is configured at all.
    #   * test_redirect — a SAFE TEST send: routed to the operator inbox
    #                     (GMAIL_REDIRECT_TO) with a '[TEST->{real_to}]' subject marker
    #                     so the test inbox shows who it WOULD have reached. The DB row's
    #                     `target` is LEFT UNCHANGED (the queue still shows the real lead).
    # A draft goes live ONLY on that explicit operator real-send; everything else still
    # redirects, so an accidental approve can never reach a real stranger. The allow-list
    # (worker gate) and exactly-once claim are unchanged. ``mode`` rides back on the
    # returned row (transient attr) so the Live-Feed can badge Live vs Test.
    real_to = action.target or ""
    subject = action.subject or ""
    redirect = os.environ.get("GMAIL_REDIRECT_TO")
    # FAIL CLOSED (wwy.3 — CRITICAL send-safety): a send goes LIVE ONLY on an
    # EXPLICIT operator live authorization (worker == 'studio_real_send'). A
    # missing GMAIL_REDIRECT_TO must NEVER be read as "go live" — that one
    # unset env var (fresh shell / new deploy / unit without the env file) was
    # the top accidental-send path, turning every routine approve into real
    # email. Mirrors the SMS invariant in connectors/sms.py.
    is_live_send = getattr(action, "worker", None) == "studio_real_send"
    if is_live_send:
        mode = "live"
        to_addr = real_to
        # subject stays CLEAN — no [TEST] marker on a real send.
    elif redirect:
        mode = "test_redirect"
        to_addr = redirect
        subject = f"[TEST->{real_to}] {subject}"
    else:
        # No redirect target AND no explicit live authorization → REFUSE. The
        # action was already claimed; mark it failed WITHOUT any external call
        # (never double-fires; exactly-once unaffected).
        blocked_reason = (
            "GMAIL_REDIRECT_TO not configured and no explicit live "
            "authorization — refusing (fail closed)"
        )
        _record_send_audit_row(
            action, mode="blocked", result="failed", transport="gmail-api",
            detail=blocked_reason, dsn=dsn,
        )
        return _with_mode(
            update_status(action.id, "failed", dsn=dsn, last_error=blocked_reason),
            "blocked",
        )

    # TOKEN GUARD (honesty): never deliver a raw template placeholder. If the body
    # still carries an unresolved {{...}} token (e.g. the copywriter's {{unsubscribe}}
    # that the studio builder should have resolved), FAIL with the reason — do NOT
    # send. Exactly-once is unaffected: the action was already claimed, this just
    # marks it failed without an external call (and never double-fires the send).
    body = action.draft or ""
    stray = _PLACEHOLDER_RE.search(body)
    if stray is not None:
        placeholder_reason = (
            f"refusing to send: unresolved template placeholder "
            f"{stray.group(0)!r} in body"
        )
        _record_send_audit_row(
            action, mode=mode, result="failed", transport="gmail-api",
            detail=placeholder_reason, dsn=dsn,
        )
        return _with_mode(
            update_status(action.id, "failed", dsn=dsn, last_error=placeholder_reason),
            mode,
        )

    # ATTACHMENT (spec §10/§13) — FAIL CLOSED. If the draft's context promised an
    # artifact (context.attachment_artifact_id / context.artwork.artifactId), load
    # its bytes and attach; ANY failure to load/validate marks the action failed
    # with the concrete reason and sends NOTHING — a promised attachment is never
    # silently dropped. No promise (absent/legacy context) → clean no-op.
    attachments: list[dict[str, Any]] = []
    attachment_receipts: tuple[Any, ...] = ()
    promised_artifact = _context_attachment_artifact_id(_action_context(action))
    if promised_artifact:
        from connectors.mail_message import MailAttachmentError, validate_attachments
        from sideeffects.artifact_media import ArtifactMediaError, load_artifact_media

        try:
            media = load_artifact_media(promised_artifact, dsn=dsn)
            attachments, attachment_receipts = validate_attachments([media.as_attachment()])
        except (ArtifactMediaError, MailAttachmentError) as exc:
            attach_reason = (
                f"draft promised attachment (artifact {promised_artifact}) but it "
                f"could not be attached: {exc} — refusing to send without it"
            )
            _record_send_audit_row(
                action, mode=mode, result="failed", transport="gmail-api",
                detail=attach_reason, dsn=dsn,
            )
            return _with_mode(
                update_status(action.id, "failed", dsn=dsn, last_error=attach_reason),
                mode,
            )
        _log.info(
            "gmail publish: action=%s attaching artifact=%s %s (source=%s)",
            action.id, promised_artifact,
            " + ".join(r.audit_label() for r in attachment_receipts),
            media.source,
        )

    _log.info(
        "gmail publish: action=%s mode=%s to=%s clean_subject=%s attachments=%d",
        action.id, mode, to_addr, mode == "live", len(attachments),
    )

    # Only pass the kwarg when an attachment exists: connectors/fakes that predate
    # attachment support keep working unchanged, and one that cannot take the
    # promised attachment fails LOUDLY (TypeError -> failed) — never a silent drop.
    send_kwargs: dict[str, Any] = {"attachments": attachments} if attachments else {}
    try:
        _ensure_real(conn)  # real-only: a mock never live-sends
        result = conn.send(
            to=to_addr,
            subject=subject,
            body=body,
            **send_kwargs,
        )
    except Exception as exc:  # noqa: BLE001 — surface the REAL error, never fake success
        # SMTP FALLBACK (Option B): only when the Gmail API is auth-dead
        # (invalid_grant / creds absent) AND an SMTP fallback is configured. All
        # gates above already ran; the fallback reuses the gated to/subject/body.
        if _gmail_auth_dead(exc):
            fallback_row = _publish_gmail_via_smtp(
                action, smtp_connector, dsn,
                to_addr=to_addr, subject=subject, body=body,
                send_kwargs=send_kwargs, attachment_receipts=attachment_receipts,
                mode=mode, primary_error=str(exc),
            )
            if fallback_row is not None:
                return fallback_row
        _record_send_audit_row(
            action, mode=mode, result="failed", transport="gmail-api",
            attachments=attachment_receipts, detail=str(exc), dsn=dsn,
        )
        return _with_mode(update_status(action.id, "failed", dsn=dsn, last_error=str(exc)), mode)
    _record_send_audit_row(
        action, mode=mode, result="sent", transport="gmail-api",
        provider_id=getattr(result, "message_id", None),
        attachments=getattr(result, "attachments", None) or attachment_receipts,
        dsn=dsn,
    )
    row = update_status(
        action.id, "sent", dsn=dsn,
        deep_link=getattr(result, "deep_link", None),
        sent_at=_now(),
        outcome_label="Sent",
        outcome_kind="success",
    )
    if row is not None:
        # Transient audit facts for the caller/UI (filename + sha256 prefix only).
        row.attachment_receipts = attachment_receipts
    return _with_mode(row, mode)


def _publish_demo(action: ActionRow, connector: Any | None, dsn: str | None) -> ActionRow:
    """SANDBOX execution channel (tlv.6): mark the approved action DELIVERED without
    any external provider, so the approve -> execute loop closes for the dummy-tenant
    demo with zero credentials. Unlike the real channels this deliberately does NOT
    call ``_ensure_real`` — a mock/sandbox connector IS the point here. Labeled
    'Delivered (sandbox)' so Runs/Activity never implies a real send happened."""
    from connectors.demo import DemoConnector

    conn = connector or DemoConnector()
    try:
        receipt = conn.deliver(
            to=action.target or "", subject=action.subject or "", body=action.draft or ""
        )
    except Exception as exc:  # noqa: BLE001 — surface the real error, never fake success
        _record_send_audit_row(
            action, mode="sandbox", result="failed", transport="sandbox",
            detail=str(exc), dsn=dsn,
        )
        return update_status(action.id, "failed", dsn=dsn, last_error=str(exc))
    _record_send_audit_row(
        action, mode="sandbox", result="sent", transport="sandbox",
        provider_id=getattr(receipt, "deep_link", None), dsn=dsn,
    )
    return _with_mode(
        update_status(
            action.id, "sent", dsn=dsn,
            deep_link=getattr(receipt, "deep_link", None),
            sent_at=_now(),
            outcome_label="Delivered (sandbox)",
            outcome_kind="success",
        ),
        "sandbox",
    )


def publish_to_meta(
    action: ActionRow, *, channel: str, image_url: str | None = None
) -> Any:
    """THE Meta Graph publish seam — deliberately NOT implemented yet.

    Reached only when :func:`meta_credentials_blocked_reason` cleared the channel
    (the operator set META_PAGE_TOKEN + META_IG_USER_ID / META_PAGE_ID) and no
    test connector was injected. Until those operator credentials are VERIFIED
    against the real Graph API, this raises instead of pretending — the action is
    then marked ``failed`` with this exact reason, so nothing fake ever
    'succeeds' and no Graph call is attempted with unverified credentials. The
    real two-step IG publish / FB feed post lands here when go-live is signed off.
    """
    raise NotImplementedError(
        "Meta Graph publish activates when operator credentials are verified"
    )


def _publish_facebook(action: ActionRow, connector: Any | None, dsn: str | None) -> ActionRow:
    try:
        if connector is None:
            # REAL path with Meta credentials present (the credential gate already
            # cleared): the Graph publish is a deliberate seam until the operator's
            # credentials are verified — it raises, and the honest failure lands
            # below. No real Graph call is attempted here.
            result = publish_to_meta(action, channel="facebook")
        else:
            _ensure_real(connector)  # real-only: a mock never live-sends
            raw = connector.send(
                action.idempotency_key or action.id,
                "facebook_feed",
                {"message": action.draft},
            )
            result = _resolve(raw)
    except Exception as exc:  # noqa: BLE001 — surface the REAL reason, never fake success
        _record_send_audit_row(
            action, mode="live", result="failed", transport="facebook-graph",
            detail=str(exc), dsn=dsn,
        )
        return update_status(action.id, "failed", dsn=dsn, last_error=str(exc))
    _record_send_audit_row(
        action, mode="live", result="sent", transport="facebook-graph",
        provider_id=getattr(result, "provider_id", None), dsn=dsn,
    )
    return update_status(
        action.id, "sent", dsn=dsn,
        deep_link=getattr(result, "deep_link", None),
        sent_at=_now(),
        outcome_label="Published",
        outcome_kind="success",
    )


def _instagram_from_env():
    from connectors.ig import InstagramConnector

    return InstagramConnector.from_env(enabled=True)


#: The exact honest refusal when a draft carries its own artwork but that artifact
#: has no publicly reachable URL (IG's Graph API pulls the image from a PUBLIC url;
#: it cannot read our local disk). Spec §11.
_IG_NO_PUBLIC_URL_ERROR = (
    "IG needs a publicly reachable image URL; local artifact not publicly served — "
    "configure PUBLIC_ASSET_BASE_URL or attach a public URL"
)


def _context_public_image_url(ctx: dict[str, Any]) -> str | None:
    """An explicit http(s) image URL staged on the action's context (the honest
    per-action media source), or ``None``. Checks the artwork block first
    (``publicUrl``/``public_url``/``imageUrl``/``image_url``/``url``), then the
    top-level context (``image_url``/``imageUrl``/``public_image_url``)."""
    candidates: list[Any] = []
    art = ctx.get("artwork")
    if isinstance(art, dict):
        candidates += [art.get(k) for k in ("publicUrl", "public_url", "imageUrl", "image_url", "url")]
    candidates += [ctx.get(k) for k in ("image_url", "imageUrl", "public_image_url")]
    for v in candidates:
        if isinstance(v, str) and v.strip().lower().startswith(("http://", "https://")):
            return v.strip()
    return None


def _resolve_ig_image_url(action: ActionRow) -> tuple[str | None, str, str | None]:
    """Resolve the PER-ACTION image URL for an IG post (spec §11); returns
    ``(image_url, source, error)`` where exactly one of ``image_url``/``error``
    is set. Resolution order — honest at every step:

    1. an explicit public URL on ``context.artwork`` / context → ``source='context_url'``;
    2. a promised artifact id (``context.artwork.artifactId`` /
       ``context.attachment_artifact_id``) + ``PUBLIC_ASSET_BASE_URL`` set →
       ``{base}/studio/artifacts/{id}/raw`` (``source='public_asset_base'``);
    3. a promised artifact with NO way to serve it publicly → the concrete
       :data:`_IG_NO_PUBLIC_URL_ERROR` refusal (a draft that promised specific
       artwork is NEVER silently published with different/global media);
    4. no per-action media at all → the legacy global ``DEMO_IG_IMAGE_URL``
       (``source='demo_env'`` — the caller logs this fallback honestly), else the
       existing concrete no-image failure."""
    ctx = _action_context(action)
    public_url = _context_public_image_url(ctx)
    if public_url:
        return public_url, "context_url", None
    artifact_id = _context_attachment_artifact_id(ctx)
    if artifact_id:
        base = (os.environ.get("PUBLIC_ASSET_BASE_URL") or "").strip()
        if base:
            return (
                f"{base.rstrip('/')}/studio/artifacts/{artifact_id}/raw",
                "public_asset_base",
                None,
            )
        return None, "artifact_not_public", f"{_IG_NO_PUBLIC_URL_ERROR} (artifact {artifact_id})"
    demo = os.environ.get("DEMO_IG_IMAGE_URL")
    if demo:
        return demo, "demo_env", None
    return None, "none", (
        "ig post needs a public image: stage per-action artwork (context.artwork with a "
        "public URL, or an artifact id + PUBLIC_ASSET_BASE_URL) or set the demo JPEG "
        "fallback DEMO_IG_IMAGE_URL — plus a valid re-minted token + Meta app review"
    )


def _publish_instagram(action: ActionRow, connector: Any | None, dsn: str | None) -> ActionRow:
    image_url, source, err = _resolve_ig_image_url(action)
    if image_url is None:
        # No publishable media. Fail honestly rather than attempt a publish that
        # cannot carry (or would swap out) the draft's media.
        _record_send_audit_row(
            action, mode="live", result="failed", transport="instagram-graph",
            detail=err, dsn=dsn,
        )
        return update_status(action.id, "failed", dsn=dsn, last_error=err)
    if source == "demo_env":
        _log.warning(
            "ig publish: action=%s has NO per-action media — falling back to the "
            "GLOBAL DEMO_IG_IMAGE_URL (%s). This is the demo fallback image, not "
            "artwork staged for this draft.",
            action.id, image_url,
        )
    else:
        _log.info("ig publish: action=%s image source=%s url=%s", action.id, source, image_url)
    try:
        if connector is None:
            # REAL path with Meta credentials present (the credential gate already
            # cleared): the Graph publish is a deliberate seam until the operator's
            # credentials are verified — it raises, and the honest failure lands
            # below. No real Graph call is attempted here.
            result = publish_to_meta(action, channel="instagram", image_url=image_url)
        else:
            _ensure_real(connector)  # real-only: a mock never live-sends
            result = _resolve(connector.post(image_url=image_url, caption=action.draft))
    except Exception as exc:  # noqa: BLE001 — surface the REAL reason, never fake success
        _record_send_audit_row(
            action, mode="live", result="failed", transport="instagram-graph",
            detail=f"{exc} (image source={source})", dsn=dsn,
        )
        return update_status(action.id, "failed", dsn=dsn, last_error=str(exc))
    _record_send_audit_row(
        action, mode="live", result="sent", transport="instagram-graph",
        provider_id=getattr(result, "media_id", None),
        detail=f"image source={source}", dsn=dsn,
    )
    return update_status(
        action.id, "sent", dsn=dsn,
        deep_link=getattr(result, "permalink", None),
        sent_at=_now(), outcome_label="Published", outcome_kind="success",
    )


def _reply_instagram(action: ActionRow, connector: Any | None, dsn: str | None) -> ActionRow:
    conn = connector or _instagram_from_env()
    try:
        _ensure_real(conn)  # real-only: a mock never live-sends
        result = _resolve(conn.reply_to_comment(comment_id=action.target or "", message=action.draft))
    except Exception as exc:  # noqa: BLE001 — surface the REAL Graph error, never fake success
        _record_send_audit_row(
            action, mode="live", result="failed", transport="instagram-graph",
            detail=str(exc), dsn=dsn,
        )
        return update_status(action.id, "failed", dsn=dsn, last_error=str(exc))
    _record_send_audit_row(
        action, mode="live", result="sent", transport="instagram-graph",
        provider_id=getattr(result, "reply_id", None), dsn=dsn,
    )
    return update_status(
        action.id, "sent", dsn=dsn,
        deep_link=getattr(result, "permalink", None) or getattr(result, "reply_id", None),
        sent_at=_now(), outcome_label="Replied", outcome_kind="success",
    )


def _resolve(value: Any) -> Any:
    """Run an awaitable connector result to completion (FB ``send`` is async)."""
    if inspect.isawaitable(value):
        import asyncio

        return asyncio.run(value)
    return value
