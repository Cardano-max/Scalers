"""Meta credential status + LIVE verification (the one-token activation probe).

``GET /studio/meta/verify`` answers, from real state only:
  1. which operator Meta env keys are set (presence booleans, never values), and
  2. whether the token ACTUALLY works — one real Graph read per id
     (``GET /{ig_user_id}?fields=id,username`` and ``GET /{page_id}?fields=id,name``)
     with the token in the Authorization header (never the URL) and
     ``appsecret_proof`` attached when META_APP_SECRET is set.

Honest by construction: a network/App-review/expired-token failure is returned
as the REAL Graph error message (token scrubbed), never as a fake "verified".
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import urllib.error
import urllib.request
from typing import Any

_GRAPH = "https://graph.facebook.com/v25.0"


def _proof(app_secret: str, token: str) -> str:
    return hmac.new(app_secret.encode(), token.encode(), hashlib.sha256).hexdigest()


def _graph_get(path: str, token: str, app_secret: str | None, fields: str) -> dict[str, Any]:
    """One Graph GET. Returns the parsed body on 2xx; raises with the REAL Graph
    error detail (token scrubbed) otherwise."""
    qs = f"fields={fields}"
    if app_secret:
        qs += f"&appsecret_proof={_proof(app_secret, token)}"
    req = urllib.request.Request(
        f"{_GRAPH}/{path}?{qs}",
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:500]
        if token in body:
            body = body.replace(token, "***")
        raise RuntimeError(f"HTTP {exc.code}: {body}") from None
    except Exception as exc:  # noqa: BLE001 — network/proxy failure, honest detail
        detail = str(exc)
        if token and token in detail:
            detail = detail.replace(token, "***")
        raise RuntimeError(detail) from None


def meta_verify() -> dict[str, Any]:
    """The full honest status: configured keys + live Graph verification results."""
    token = (os.environ.get("META_PAGE_TOKEN") or "").strip()
    ig_id = (os.environ.get("META_IG_USER_ID") or "").strip()
    page_id = (os.environ.get("META_PAGE_ID") or "").strip()
    app_secret = (os.environ.get("META_APP_SECRET") or "").strip() or None

    out: dict[str, Any] = {
        "configured": {
            "META_PAGE_TOKEN": bool(token),
            "META_IG_USER_ID": bool(ig_id),
            "META_PAGE_ID": bool(page_id),
            "META_APP_SECRET": bool(app_secret),
        },
        "instagram": {"verified": False, "detail": None},
        "facebook": {"verified": False, "detail": None},
    }
    if not token:
        out["instagram"]["detail"] = out["facebook"]["detail"] = (
            "META_PAGE_TOKEN is not set — nothing to verify"
        )
        return out

    if ig_id:
        try:
            data = _graph_get(ig_id, token, app_secret, "id,username")
            out["instagram"] = {
                "verified": str(data.get("id")) == ig_id,
                "detail": f"@{data.get('username')}" if data.get("username") else str(data),
            }
        except RuntimeError as exc:
            out["instagram"] = {"verified": False, "detail": str(exc)}
    else:
        out["instagram"]["detail"] = "META_IG_USER_ID is not set"

    if page_id:
        try:
            data = _graph_get(page_id, token, app_secret, "id,name")
            out["facebook"] = {
                "verified": str(data.get("id")) == page_id,
                "detail": str(data.get("name") or data),
            }
        except RuntimeError as exc:
            out["facebook"] = {"verified": False, "detail": str(exc)}
    else:
        out["facebook"]["detail"] = "META_PAGE_ID is not set"

    return out
