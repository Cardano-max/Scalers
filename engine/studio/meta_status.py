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


def _verify_id(
    graph_id: str, token: str, app_secret: str | None, fields: str,
    render: Any,
) -> dict[str, Any]:
    """Verify ONE Graph id, separating the two failure classes an operator hit:

    * token bad → verified False with the real Graph error;
    * token GOOD but META_APP_SECRET belongs to a DIFFERENT app → Meta rejects the
      appsecret_proof signature. A plain probe read that as ``verified: false``,
      which points the operator at the (perfectly valid) token. Here we retry
      WITHOUT the proof to prove the token itself, and report the mismatch on
      ``appsecretProof`` — publishing stays blocked until the secret is fixed
      (the publish connectors ALWAYS sign; they never drop the proof)."""
    if app_secret:
        try:
            data = _graph_get(graph_id, token, app_secret, fields)
            return {
                "verified": str(data.get("id")) == graph_id,
                "detail": render(data),
                "appsecretProof": "ok",
            }
        except RuntimeError as exc:
            if "appsecret_proof" not in str(exc).lower():
                return {"verified": False, "detail": str(exc), "appsecretProof": "unknown"}
            # Proof rejected — prove the token on its own so the diagnosis is exact.
            try:
                data = _graph_get(graph_id, token, None, fields)
                return {
                    "verified": str(data.get("id")) == graph_id,
                    "detail": render(data),
                    "appsecretProof": (
                        "MISMATCH — the token is valid, but META_APP_SECRET does not "
                        "belong to the app that issued META_PAGE_TOKEN. Publishing "
                        "will FAIL until the matching app secret is set (every "
                        "publish call is signed)."
                    ),
                }
            except RuntimeError as exc2:
                return {"verified": False, "detail": str(exc2), "appsecretProof": "mismatch"}
    try:
        data = _graph_get(graph_id, token, None, fields)
        return {
            "verified": str(data.get("id")) == graph_id,
            "detail": render(data),
            "appsecretProof": (
                "MISSING — META_APP_SECRET is not set; the publish connectors "
                "require it (every publish call is signed), so publishing will "
                "refuse until it is configured."
            ),
        }
    except RuntimeError as exc:
        return {"verified": False, "detail": str(exc), "appsecretProof": "missing"}


def meta_verify() -> dict[str, Any]:
    """The full honest status: configured keys + live Graph verification results.

    ``publishReady`` is True only when the PROOF-SIGNED call succeeded — a valid
    token with a mismatched/missing app secret verifies the account but cannot
    publish, and this endpoint must say exactly that."""
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
        "publishReady": False,
    }
    if not token:
        out["instagram"]["detail"] = out["facebook"]["detail"] = (
            "META_PAGE_TOKEN is not set — nothing to verify"
        )
        return out

    if ig_id:
        out["instagram"] = _verify_id(
            ig_id, token, app_secret, "id,username",
            lambda d: f"@{d.get('username')}" if d.get("username") else str(d),
        )
    else:
        out["instagram"]["detail"] = "META_IG_USER_ID is not set"

    if page_id:
        out["facebook"] = _verify_id(
            page_id, token, app_secret, "id,name",
            lambda d: str(d.get("name") or d),
        )
    else:
        out["facebook"]["detail"] = "META_PAGE_ID is not set"

    out["publishReady"] = (
        out["instagram"].get("appsecretProof") == "ok"
        or out["facebook"].get("appsecretProof") == "ok"
    )
    return out
