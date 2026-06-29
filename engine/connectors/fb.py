"""Facebook / Meta Graph connector scaffold (bead xeu) — gated, mock-default.

Implements the side-effect ``Connector`` contract for IG/FB publish + comment
replies. Least-privilege scopes (sec req A); ``appsecret_proof`` on EVERY Graph
call (req C); token key-from-pack, header/body never URL, never logged (req B);
official-host-only + pin-to-IP via the de6 boundary (req D). DM/comment replies
hard-escalate to a human (req E). Disabled by default → no live call.
"""

from __future__ import annotations

import json

from connectors.base import (
    ConnectorHeldError,
    GatedConnector,
    appsecret_proof,
    redact,
    verify_x_hub_signature_256,
)
from sideeffects.provider import ProviderResult

GRAPH_API_BASE = "https://graph.facebook.com"
_GRAPH_HOST = "graph.facebook.com"
_GRAPH_VERSION = "v25.0"

# Least-privilege scopes (sec req A / dhv.9) — request ONLY these; each justified.
FB_SCOPES: tuple[str, ...] = (
    "instagram_basic",              # read IG account
    "instagram_content_publish",    # publish posts/reels
    "instagram_manage_comments",    # read/reply comments
    "pages_show_list",              # list the managed Page
    "pages_read_engagement",        # read Page engagement
    "pages_manage_engagement",      # reply to comments (not pages_manage_posts)
)
# NOT requested: ads_* write (REJECTED-class), publish_to_groups, business_management.

# Channels that are outbound conversation (DM) -> always hard-escalate (req E).
_DM_CHANNELS = frozenset({"instagram_dm", "messenger_dm", "dm"})


class FacebookConnector(GatedConnector):
    """Meta Graph connector. Live publish path is wired but OFF (sec go-live gate)."""

    name = "facebook"
    provider_name = "facebook"

    def __init__(self, *, page_token: str | None = None, app_secret: str | None = None,
                 **kw) -> None:
        # key-from-pack (LADIES8391_FB_PAGE_TOKEN) + app secret (app-level); never
        # a vendored .env, never logged.
        super().__init__(**kw)
        self._page_token = page_token
        self._app_secret = app_secret

    def __repr__(self) -> str:  # never leak the token in a repr
        return f"FacebookConnector(enabled={self._enabled}, token={redact(self._page_token)})"

    def verify_webhook(self, raw_body: bytes, x_hub_signature_256: str | None) -> bool:
        """Meta webhook ingress is untrusted — verify the signature, reject forged."""
        if not self._app_secret:
            return False
        return verify_x_hub_signature_256(self._app_secret, raw_body, x_hub_signature_256)

    async def send(self, key: str, channel: str, payload: dict) -> ProviderResult:
        # DMs always escalate to a human (req E) — even when enabled.
        if channel in _DM_CHANNELS:
            raise ConnectorHeldError(
                f"facebook DM ({channel}) is HELD — hard-escalate to human (439); "
                "no autonomous send."
            )
        self._require_enabled()
        if not (self._page_token and self._app_secret):
            raise ConnectorHeldError("facebook connector missing key-from-pack token/secret")

        # appsecret_proof on EVERY Graph call; token + proof in the POST BODY, never
        # the URL (sec req B/C). `key` is the idempotency token (exactly-once).
        proof = appsecret_proof(self._app_secret, self._page_token)
        body = json.dumps({
            "access_token": self._page_token,   # body, not ?access_token=
            "appsecret_proof": proof,
            "idempotency_key": key,
            **payload,
        }).encode("utf-8")
        path = f"/{_GRAPH_VERSION}/me/media"  # representative publish endpoint
        resp = self._secure_request(
            api_base=GRAPH_API_BASE, host=_GRAPH_HOST, method="POST", path=path,
            headers={"Content-Type": "application/json"}, body=body,
        )
        if resp.status >= 400:
            # error message must not echo the token/proof
            raise RuntimeError(f"Graph API HTTP {resp.status} on {channel}")
        data = _safe_json(resp.body)
        pid = str(data.get("id", "")) if isinstance(data, dict) else ""
        return ProviderResult(
            provider_id=pid or key,
            external_id=pid or None,
            deep_link=f"https://www.facebook.com/{pid}" if pid else None,
        )


def _safe_json(raw: str) -> dict:
    try:
        out = json.loads(raw)
        return out if isinstance(out, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}
