#!/usr/bin/env python3
"""Mint the long-lived Meta PAGE token the IG/FB connectors need — one command.

Run ON YOUR OWN MACHINE (graph.facebook.com is blocked from the cloud sandbox):

    python3 scripts/meta_mint_page_token.py [path/to/.env]

Reads from the env file (default: engine/.env):
    META_APP_ID, META_APP_SECRET, META_USER_ACCESS_TOKEN  (the Graph-Explorer
    user token — short-lived, so run this within ~1 hour of minting it)

Does, in order (all read-only Graph calls):
    1. verify the user token (GET /me)
    2. exchange it for a LONG-LIVED user token (~60 days)
    3. list your Pages (GET /me/accounts) — page tokens minted from a
       long-lived user token do not expire
    4. read each Page's linked instagram_business_account
    5. append META_PAGE_ACCESS_TOKEN / META_PAGE_ID_RESOLVED /
       IG_BUSINESS_ACCOUNT_ID_RESOLVED to the SAME env file

Never prints token values — only names/ids/status.
"""

from __future__ import annotations

import sys
from pathlib import Path

import requests

GRAPH = "https://graph.facebook.com/v25.0"


def _load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip()
    return env


def main() -> int:
    env_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "engine" / ".env"
    if not env_path.exists():
        print(f"env file not found: {env_path}")
        return 1
    env = _load_env(env_path)
    missing = [k for k in ("META_APP_ID", "META_APP_SECRET", "META_USER_ACCESS_TOKEN") if not env.get(k)]
    if missing:
        print(f"missing in {env_path.name}: {', '.join(missing)}")
        return 1

    def get(path: str, **params):
        r = requests.get(f"{GRAPH}{path}", params=params, timeout=30)
        return r.status_code, r.json()

    s, me = get("/me", access_token=env["META_USER_ACCESS_TOKEN"], fields="id,name")
    if s != 200:
        print(f"user token INVALID/expired ({s}): {me.get('error', {}).get('message', '')[:160]}")
        print("Mint a fresh one in Graph API Explorer (same 7 permissions) and re-run.")
        return 1
    print(f"user token OK — {me.get('name')} ({me.get('id')})")

    s, ll = get(
        "/oauth/access_token",
        grant_type="fb_exchange_token",
        client_id=env["META_APP_ID"],
        client_secret=env["META_APP_SECRET"],
        fb_exchange_token=env["META_USER_ACCESS_TOKEN"],
    )
    if s != 200 or not ll.get("access_token"):
        print(f"long-lived exchange FAILED ({s}): {ll.get('error', {}).get('message', '')[:160]}")
        return 1
    long_lived = ll["access_token"]
    print("long-lived user token minted")

    s, pages = get("/me/accounts", access_token=long_lived, fields="id,name,access_token,instagram_business_account")
    if s != 200:
        print(f"pages fetch FAILED ({s}): {pages.get('error', {}).get('message', '')[:160]}")
        return 1
    data = pages.get("data", [])
    if not data:
        print("NO Pages on this account — make sure your Facebook user has a role on the studio's Page.")
        return 1
    print(f"pages: {len(data)}")
    chosen = None
    for p in data:
        iba = (p.get("instagram_business_account") or {}).get("id")
        print(f"  - {p.get('name')}  page_id={p.get('id')}  ig_business_account={iba or 'NOT LINKED'}")
        if chosen is None and p.get("access_token"):
            chosen = p
    if chosen is None:
        print("no page token returned — the user token likely lacks pages_show_list")
        return 1

    iba = (chosen.get("instagram_business_account") or {}).get("id")
    with open(env_path, "a", encoding="utf-8") as f:
        f.write(f"\n# minted by scripts/meta_mint_page_token.py\n")
        f.write(f"META_PAGE_ACCESS_TOKEN={chosen['access_token']}\n")
        f.write(f"META_PAGE_ID_RESOLVED={chosen['id']}\n")
        if iba:
            f.write(f"IG_BUSINESS_ACCOUNT_ID_RESOLVED={iba}\n")
    print(f"saved PAGE token for '{chosen.get('name')}' -> {env_path} (values not printed)")
    if not iba:
        print("WARNING: no instagram_business_account linked to that Page — link it in Meta Business Suite for IG publishing.")
    print("Restart the engine to pick the new values up.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
