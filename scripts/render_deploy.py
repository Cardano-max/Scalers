#!/usr/bin/env python3
"""Provision + deploy the full Scalers stack on Render via its REST API.

Runs on a GitHub Actions runner (the dev sandbox cannot reach api.render.com).
Idempotent: find-or-create each resource by name, then wait for the deploys.

    RENDER_API_KEY=...  SCALERS_ENV="$(cat .env)"  python3 scripts/render_deploy.py

Env:
    RENDER_API_KEY   required — Render API key (repo Actions secret).
    SCALERS_ENV      required — full contents of the operator envfile; parsed
                     into the engine service's runtime env vars. Values are
                     NEVER printed (names only).
    DEPLOY_BRANCH    branch Render tracks (default: the current checkout's).
    RENDER_REGION    default oregon.
    RENDER_PLAN_WEB  default starter   (set free for a no-card account).
    RENDER_PLAN_DB   default basic_256mb.
    SKIP_CONSOLE=1   engine+db only (console on Vercel instead).

Stdlib only — no pip installs on the runner.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

API = "https://api.render.com/v1"
REPO_URL = "https://github.com/Cardano-max/Scalers"

# Engine runtime env vars taken from SCALERS_ENV (exact operator envfile names).
ENGINE_SECRET_KEYS = [
    "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "FIRECRAWL_API_KEY", "FOREPLAY_API_KEY",
    "META_APP_ID", "META_APP_SECRET", "META_BUSINESS_PORTFOLIO_ID", "META_PAGE_ID",
    "IG_BUSINESS_ACCOUNT_ID", "INK_STUDIO_META_ACCESS_TOKEN",
    "LADIES8391_META_ACCESS_TOKEN", "LADIES8391_FB_PAGE_TOKEN",
    "GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "LADIES8391_GMAIL_OAUTH_REFRESH",
    "SMTP_SENDER", "SMTP_APP_PASSWORD", "GMAIL_REDIRECT_TO",
    "META_USER_ACCESS_TOKEN", "META_IG_USER_ID", "META_PAGE_TOKEN",
]


def _req(method: str, path: str, body: dict | None = None) -> dict | list:
    url = path if path.startswith("http") else API + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "authorization": f"Bearer {os.environ['RENDER_API_KEY']}",
        "accept": "application/json",
        "content-type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read().decode()
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:2000]
        raise RuntimeError(f"{method} {path} -> HTTP {e.code}: {detail}") from None


def parse_envfile(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in (text or "").splitlines():
        line = line.strip().lstrip("﻿")
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and v:
            out[k] = v
    return out


def find_by_name(path: str, name: str, unwrap: str) -> dict | None:
    """First resource whose name matches, across cursor pages."""
    cursor = ""
    for _ in range(20):
        q = f"{path}{'&' if '?' in path else '?'}limit=50" + (f"&cursor={cursor}" if cursor else "")
        page = _req("GET", q)
        if not isinstance(page, list) or not page:
            return None
        for item in page:
            res = item.get(unwrap) or item
            if res.get("name") == name:
                return res
        cursor = page[-1].get("cursor") or ""
        if not cursor:
            return None
    return None


def wait(desc: str, fn, ok, bad=(), timeout_s: int = 1500) -> dict:
    t0 = time.time()
    while True:
        cur = fn()
        state = ok(cur)
        if state is True:
            print(f"  {desc}: ready")
            return cur
        if state in bad:
            raise RuntimeError(f"{desc}: reached failure state {state!r}: {json.dumps(cur)[:800]}")
        if time.time() - t0 > timeout_s:
            raise RuntimeError(f"{desc}: timed out after {timeout_s}s (last: {json.dumps(cur)[:400]})")
        print(f"  {desc}: {state} …")
        time.sleep(15)


def latest_deploy(service_id: str) -> dict:
    deploys = _req("GET", f"/services/{service_id}/deploys?limit=1")
    if isinstance(deploys, list) and deploys:
        return deploys[0].get("deploy") or deploys[0]
    return {}


def wait_deploy_live(name: str, service_id: str) -> None:
    def check(cur: dict):
        status = cur.get("status") or "no-deploy-yet"
        if status == "live":
            return True
        if status in ("build_failed", "update_failed", "canceled", "deactivated", "pre_deploy_failed"):
            return status
        return status

    wait(f"{name} deploy", lambda: latest_deploy(service_id), check,
         bad=("build_failed", "update_failed", "canceled", "deactivated", "pre_deploy_failed"))


def main() -> int:
    if not os.environ.get("RENDER_API_KEY"):
        print("FATAL: RENDER_API_KEY missing (add it as a repo Actions secret)")
        return 2
    envfile = parse_envfile(os.environ.get("SCALERS_ENV", ""))
    if not envfile:
        print("FATAL: SCALERS_ENV missing/empty (add the operator envfile contents "
              "as a repo Actions secret)")
        return 2
    branch = os.environ.get("DEPLOY_BRANCH") or "main"
    region = os.environ.get("RENDER_REGION", "oregon")
    plan_web = os.environ.get("RENDER_PLAN_WEB", "starter")
    plan_db = os.environ.get("RENDER_PLAN_DB", "basic_256mb")

    owners = _req("GET", "/owners?limit=20")
    owner_id = (owners[0].get("owner") or owners[0])["id"] if owners else None
    if not owner_id:
        print("FATAL: no Render owner visible to this API key")
        return 2
    print(f"owner: {owner_id} | branch: {branch} | region: {region}")

    # ── 1. Postgres ────────────────────────────────────────────────────────────
    # An EXTERNAL managed Postgres (Neon/Supabase — no card needed) short-circuits
    # Render's database billing wall: set the EXTERNAL_DATABASE_URL repo secret.
    db_url = (os.environ.get("EXTERNAL_DATABASE_URL") or "").strip()
    if db_url:
        print("postgres: using EXTERNAL_DATABASE_URL (Neon/Supabase — not printed)")
    else:
        db = find_by_name("/postgres", "scalers-db", "postgres")
        if db is None:
            print("creating postgres scalers-db …")
            try:
                db = _req("POST", "/postgres", {
                    "name": "scalers-db", "ownerId": owner_id, "plan": plan_db,
                    "region": region, "version": "16",
                })
            except RuntimeError as exc:
                if "402" in str(exc):
                    print(
                        "FATAL: Render requires payment info on file to create a "
                        "database (free web services don't, databases do).\n"
                        "Fix EITHER way:\n"
                        "  a) add a card at https://dashboard.render.com/billing "
                        "and re-run, OR\n"
                        "  b) create a free Neon database (no card): neon.tech → "
                        "new project → copy the connection string → add it as the "
                        "EXTERNAL_DATABASE_URL repo Actions secret and re-run."
                    )
                    return 3
                raise
            db = db.get("postgres") or db
        db_id = db["id"]
        wait("postgres", lambda: _req("GET", f"/postgres/{db_id}"),
             lambda cur: True if (cur.get("postgres") or cur).get("status") == "available"
             else (cur.get("postgres") or cur).get("status", "creating"))
        conn = _req("GET", f"/postgres/{db_id}/connection-info")
        db_url = conn.get("internalConnectionString") or conn.get("externalConnectionString")
        if not db_url:
            print(f"FATAL: no connection string in {list(conn.keys())}")
            return 2
        print("postgres: connection string obtained (not printed)")

    # ── 2. Engine (Docker) ─────────────────────────────────────────────────────
    engine_env = [{"key": "ENGINE_DATABASE_URL", "value": db_url},
                  {"key": "STUDIO_TENANT_ID", "value": envfile.get("STUDIO_TENANT_ID", "ladies8391")},
                  {"key": "SCALERS_EMBEDDER", "value": "deterministic"}]
    present, missing = [], []
    for key in ENGINE_SECRET_KEYS:
        if envfile.get(key):
            engine_env.append({"key": key, "value": envfile[key]})
            present.append(key)
        else:
            missing.append(key)
    print(f"engine env: {len(present)} secrets from envfile; missing (ok if unused): {missing}")

    engine = find_by_name("/services?type=web_service", "scalers-engine", "service")
    if engine is None:
        print("creating scalers-engine …")
        details: dict = {
            "env": "docker", "plan": plan_web, "region": region,
            "healthCheckPath": "/healthz",
            "envSpecificDetails": {"dockerfilePath": "./Dockerfile.engine",
                                   "dockerContext": "."},
        }
        # Persistent disks are a paid-plan feature; on the free plan uploads under
        # var/artifacts survive requests but not redeploys (honest limitation).
        if plan_web != "free":
            details["disk"] = {"name": "artifacts", "mountPath": "/app/engine/var",
                               "sizeGB": 5}
        else:
            print("free plan: skipping persistent disk (artwork uploads won't "
                  "survive a redeploy until the service is upgraded)")
        created = _req("POST", "/services", {
            "type": "web_service", "name": "scalers-engine", "ownerId": owner_id,
            "repo": REPO_URL, "branch": branch, "autoDeploy": "yes", "rootDir": "",
            "envVars": engine_env,
            "serviceDetails": details,
        })
        engine = created.get("service") or created
    else:
        print("scalers-engine exists — syncing env vars + redeploying")
        _req("PUT", f"/services/{engine['id']}/env-vars", engine_env)
        _req("POST", f"/services/{engine['id']}/deploys", {})
    engine_id = engine["id"]
    engine_url = (engine.get("serviceDetails") or {}).get("url") or "https://scalers-engine.onrender.com"
    wait_deploy_live("engine", engine_id)
    print(f"ENGINE LIVE: {engine_url}")

    # ── 3. Console (Node) ──────────────────────────────────────────────────────
    if os.environ.get("SKIP_CONSOLE") == "1":
        print("SKIP_CONSOLE=1 — console not provisioned (Vercel path)")
        print(f"\nDONE. engine={engine_url}")
        return 0
    console_env = [
        {"key": "NODE_VERSION", "value": "20"},
        {"key": "STUDIO_BACKEND_ORIGIN", "value": engine_url},
        {"key": "NEXT_PUBLIC_DATA_SOURCE", "value": "live"},
        {"key": "NEXT_PUBLIC_TENANT_ID", "value": envfile.get("STUDIO_TENANT_ID", "ladies8391")},
    ]
    console = find_by_name("/services?type=web_service", "scalers-console", "service")
    if console is None:
        print("creating scalers-console …")
        created = _req("POST", "/services", {
            "type": "web_service", "name": "scalers-console", "ownerId": owner_id,
            "repo": REPO_URL, "branch": branch, "autoDeploy": "yes", "rootDir": "web",
            "envVars": console_env,
            "serviceDetails": {
                "env": "node", "plan": plan_web, "region": region,
                "envSpecificDetails": {"buildCommand": "npm ci && npm run build",
                                       "startCommand": "npm run start"},
            },
        })
        console = created.get("service") or created
    else:
        print("scalers-console exists — syncing env vars + redeploying")
        _req("PUT", f"/services/{console['id']}/env-vars", console_env)
        _req("POST", f"/services/{console['id']}/deploys", {})
    console_id = console["id"]
    console_url = (console.get("serviceDetails") or {}).get("url") or "https://scalers-console.onrender.com"
    wait_deploy_live("console", console_id)

    print(f"\nDONE.\n  engine : {engine_url}\n  console: {console_url}\n"
          f"  healthz: {engine_url}/healthz")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
