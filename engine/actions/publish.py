"""Approve → publish: send an operator-approved action via the real connector.

This is the human-authorized side-effect path of the live demo. The console's
``approveAction`` mutation calls :func:`approve_and_publish`; ``rejectAction``
calls :func:`reject`. Connector selection is by the action's ``channel``:

* ``gmail``     → :class:`connectors.gmail.GmailConnector` (enabled, real creds
  from env) → a **real** ``users.messages.send`` → ``status='sent'`` + deep_link.
* ``facebook``  → :class:`connectors.fb.FacebookConnector` (enabled, real creds
  from env). The Meta page token is currently expired, so this raises the REAL
  Graph error → ``status='failed'`` + ``last_error``. **Never a fake success.**
* ``instagram`` → :class:`connectors.ig.InstagramConnector` — ``post`` (the
  2-step media→media_publish flow) for posts, ``reply_to_comment`` for comment
  replies. The Meta token is currently expired, so a live call raises the REAL
  Graph error → ``status='failed'`` + ``last_error``. **Never a fake publish.**
  (An IG post additionally needs a public JPEG ``DEMO_IG_IMAGE_URL`` and, for a
  non-test user, Meta App Review of ``instagram_content_publish``.)

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
"""

from __future__ import annotations

import inspect
import os
from datetime import datetime, timezone
from typing import Any

from actions.store import ActionRow, get_action, update_status


class ActionNotFoundError(LookupError):
    """``approve_and_publish``/``reject`` was given an unknown action id."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def approve_and_publish(
    action_id: str,
    *,
    connectors: dict[str, Any] | None = None,
    dsn: str | None = None,
) -> ActionRow:
    """Approve an action and publish it through the channel's real connector.

    Returns the updated :class:`ActionRow`. Idempotent on a sent action (returns
    it unchanged, no second send). A connector failure marks the action ``failed``
    with the real error — it is never reported as a success.
    """
    connectors = connectors or {}
    action = get_action(action_id, dsn=dsn)
    if action is None:
        raise ActionNotFoundError(action_id)

    # Exactly-once: a sent action is terminal — return it, do not re-send.
    if action.status == "sent":
        return action

    # Operator authorization recorded before the external call.
    action = update_status(
        action_id, "approved", dsn=dsn, autonomy="approved", approved_at=_now()
    )

    channel = (action.channel or "").lower()
    atype = (action.type or "").lower()
    if channel == "gmail":
        return _publish_gmail(action, connectors.get("gmail"), dsn)
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


def _publish_gmail(action: ActionRow, connector: Any | None, dsn: str | None) -> ActionRow:
    from connectors.gmail import GmailConnector

    conn = connector or GmailConnector.from_env(enabled=True)
    try:
        result = conn.send(
            to=action.target or "",
            subject=action.subject or "",
            body=action.draft,
        )
    except Exception as exc:  # noqa: BLE001 — surface the REAL error, never fake success
        return update_status(action.id, "failed", dsn=dsn, last_error=str(exc))
    return update_status(
        action.id, "sent", dsn=dsn,
        deep_link=getattr(result, "deep_link", None),
        sent_at=_now(),
        outcome_label="Sent",
        outcome_kind="success",
    )


def _publish_facebook(action: ActionRow, connector: Any | None, dsn: str | None) -> ActionRow:
    conn = connector or _facebook_from_env()
    try:
        raw = conn.send(
            action.idempotency_key or action.id,
            "facebook_feed",
            {"message": action.draft},
        )
        result = _resolve(raw)
    except Exception as exc:  # noqa: BLE001 — expired token raises the REAL Graph error
        return update_status(action.id, "failed", dsn=dsn, last_error=str(exc))
    return update_status(
        action.id, "sent", dsn=dsn,
        deep_link=getattr(result, "deep_link", None),
        sent_at=_now(),
        outcome_label="Published",
        outcome_kind="success",
    )


def _facebook_from_env(env: dict[str, str] | None = None):
    from connectors.fb import FacebookConnector

    e = env if env is not None else os.environ
    return FacebookConnector(
        enabled=True,
        page_token=e.get("LADIES8391_FB_PAGE_TOKEN"),
        app_secret=e.get("META_APP_SECRET"),
        page_id=e.get("LADIES8391_FB_PAGE_ID") or e.get("META_PAGE_ID"),
    )


def _instagram_from_env():
    from connectors.ig import InstagramConnector

    return InstagramConnector.from_env(enabled=True)


def _publish_instagram(action: ActionRow, connector: Any | None, dsn: str | None) -> ActionRow:
    conn = connector or _instagram_from_env()
    image_url = os.environ.get("DEMO_IG_IMAGE_URL")
    if not image_url:
        # IG content publishing requires a public JPEG container source. Fail
        # honestly rather than attempt a publish that cannot carry media.
        return update_status(
            action.id, "failed", dsn=dsn,
            last_error="ig post needs a public JPEG (set DEMO_IG_IMAGE_URL) + a valid re-minted token + Meta app review",
        )
    try:
        result = _resolve(conn.post(image_url=image_url, caption=action.draft))
    except Exception as exc:  # noqa: BLE001 — surface the REAL Graph error, never fake success
        return update_status(action.id, "failed", dsn=dsn, last_error=str(exc))
    return update_status(
        action.id, "sent", dsn=dsn,
        deep_link=getattr(result, "permalink", None),
        sent_at=_now(), outcome_label="Published", outcome_kind="success",
    )


def _reply_instagram(action: ActionRow, connector: Any | None, dsn: str | None) -> ActionRow:
    conn = connector or _instagram_from_env()
    try:
        result = _resolve(conn.reply_to_comment(comment_id=action.target or "", message=action.draft))
    except Exception as exc:  # noqa: BLE001 — surface the REAL Graph error, never fake success
        return update_status(action.id, "failed", dsn=dsn, last_error=str(exc))
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
