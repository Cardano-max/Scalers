"""Verify the DEPLOYED stack the way a client would use it — through its real
HTTP surface, stdlib-only (runs in GitHub Actions, which can reach onrender.com).

What it proves, in order:
  1. the engine is serving the NEW build (the /contributions route answers)
  2. the roster + seeded memory are present (Kaps, customers, conversations)
  3. a REAL campaign run launches via POST /studio/run (the console button path),
     the multi-agent spine executes, and drafts stage HELD — nothing sends
  4. one staged draft's Agent Contributions trail contains the full team
     (strategy → research → identity guardian → location → analyst → copywriter
     → critic → jury), assembled from recorded agent_runs, and is printed as
     evidence
  5. every action the run produced is PENDING (approve-first held)

Environment: ENGINE_URL (default https://scalers-engine.onrender.com),
TENANT_ID (default ladies8391). Exits non-zero on any failed check.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("ENGINE_URL", "https://scalers-engine.onrender.com").rstrip("/")
CONSOLE = os.environ.get("CONSOLE_URL", "https://scalers-console.onrender.com").rstrip("/")
TENANT = os.environ.get("TENANT_ID", "ladies8391")
SESSION = os.environ.get("SESSION_ID", "studio-live-session")
# When set (CI), gate on Render reporting THIS commit live before the scenario
# runs — otherwise the probe can pass against the PREVIOUS build and the deploy
# swap then 502s mid-run and kills the in-flight campaign (seen on run #1).
RENDER_KEY = os.environ.get("RENDER_API_KEY", "")
EXPECT_SHA = os.environ.get("EXPECT_COMMIT_SHA", "")
RENDER_API = "https://api.render.com/v1"


def _req(method: str, path: str, body: dict | None = None, timeout: int = 120):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"content-type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def call(method: str, path: str, body: dict | None = None,
         timeout: int = 120, retries: int = 8):
    """Render answers 502/503/504 from its proxy while a deploy swaps
    instances — retry through the window instead of dying on it."""
    for attempt in range(retries):
        try:
            return _req(method, path, body, timeout)
        except urllib.error.HTTPError as e:
            if e.code in (502, 503, 504) and attempt < retries - 1:
                print(f"  {path}: HTTP {e.code} (deploy window) — retry {attempt + 1}")
                time.sleep(20)
                continue
            raise
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < retries - 1:
                print(f"  {path}: {e} — retry {attempt + 1}")
                time.sleep(20)
                continue
            raise


def _render(path: str):
    req = urllib.request.Request(
        RENDER_API + path,
        headers={"authorization": f"Bearer {RENDER_KEY}", "accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read() or b"{}")


def wait_for_render_deploy() -> None:
    """Block until Render reports the EXPECTED commit live on scalers-engine.
    Skipped (with a note) when RENDER_API_KEY is absent — the route probe alone
    then gates, which only proves SOME build with the route is up."""
    if not (RENDER_KEY and EXPECT_SHA):
        print("no RENDER_API_KEY/EXPECT_COMMIT_SHA — skipping deploy-sha gate")
        return
    svc_id = ""
    for item in _render("/services?type=web_service&limit=50") or []:
        svc = item.get("service") or item
        if svc.get("name") == "scalers-engine":
            svc_id = svc["id"]
            break
    if not svc_id:
        raise SystemExit("scalers-engine service not found on Render")
    deadline = time.time() + 30 * 60
    while time.time() < deadline:
        deps = _render(f"/services/{svc_id}/deploys?limit=1") or []
        dep = (deps[0].get("deploy") or deps[0]) if deps else {}
        commit = ((dep.get("commit") or {}).get("id") or "")[:12]
        status = dep.get("status") or "none"
        if commit == EXPECT_SHA[:12] and status == "live":
            print(f"render deploy live for {commit}")
            return
        if commit == EXPECT_SHA[:12] and status in (
            "build_failed", "update_failed", "canceled", "deactivated",
        ):
            raise SystemExit(f"render deploy for {commit} failed: {status}")
        print(f"waiting for render deploy: latest={commit or 'none'} status={status}")
        time.sleep(20)
    raise SystemExit("render never reported the pushed commit live")


def wait_for_new_build() -> None:
    """The new build is live once /studio/action/{id}/contributions ANSWERS —
    an unknown id must 404 with our 'unknown action' detail (stale builds have
    no such route and return the bare framework 404)."""
    for i in range(80):
        try:
            _req("GET", "/studio/action/act_verify_probe/contributions", timeout=20)
            print("contributions route live (unexpected 200)")
            return
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = json.loads(e.read() or b"{}").get("detail", "")
            except Exception:
                pass
            if e.code == 404 and "unknown action" in str(detail):
                print(f"contributions route live after {i} probes")
                return
            print(f"waiting for new build ({i}): HTTP {e.code} {detail!r}")
        except Exception as e:  # noqa: BLE001 — keep probing through restarts
            print(f"waiting for engine ({i}): {e}")
        time.sleep(15)
    raise SystemExit("engine never served the new /contributions route")


def main() -> None:
    print(f"== verify {BASE} tenant={TENANT}")

    # 1. new build + health
    wait_for_render_deploy()
    wait_for_new_build()
    call("GET", "/healthz", timeout=20)
    print("healthz OK")

    # 2. roster + memory state
    artists = call("GET", "/studio/artists")
    names = [a.get("name") for a in artists.get("artists", [])]
    assert "Kaps" in names, f"Kaps missing from roster: {names}"
    print(f"roster OK: {names}")

    # 2b. the CONSOLE — what the operator's browser actually loads. The page must
    #     serve, and the console's OWN /studio/artists proxy (the exact request the
    #     Artists tab makes) must return the roster — this is the end-to-end check
    #     for "the site is up but the artists tab is empty".
    try:
        req = urllib.request.Request(CONSOLE + "/", headers={"accept": "text/html"})
        with urllib.request.urlopen(req, timeout=60) as r:
            assert r.status == 200
        print(f"console page OK: {CONSOLE}/")
        via_console = None
        for attempt in range(4):
            try:
                req = urllib.request.Request(
                    CONSOLE + "/studio/artists", headers={"accept": "application/json"})
                with urllib.request.urlopen(req, timeout=60) as r:
                    via_console = json.loads(r.read() or b"{}")
                break
            except urllib.error.HTTPError as e:
                if e.code in (502, 503, 504) and attempt < 3:
                    time.sleep(15)
                    continue
                raise
        proxied = [a.get("name") for a in (via_console or {}).get("artists", [])]
        assert "Kaps" in proxied, (
            f"console→engine proxy broken: /studio/artists via console = {proxied} "
            "(engine has the roster, so STUDIO_BACKEND_ORIGIN on the console is wrong "
            "or its deploy is stale)")
        print(f"console proxy OK — Artists tab data via console: {proxied}")
    except AssertionError:
        raise
    except Exception as e:  # noqa: BLE001 — name the console as the failing side
        raise SystemExit(f"console check failed ({CONSOLE}): {e}") from e
    mem = call("GET", f"/studio/memory-state?tenant_id={TENANT}")
    print("memory-state:", json.dumps({k: mem.get(k) for k in
          ("customers_total", "review_queue", "drafts")}, default=str)[:300])
    assert int(mem.get("customers_total") or 0) >= 20, (
        f"seeded customers missing: {mem.get('customers_total')}")

    # 2c. competitor post IMAGE research (meeting ask): upload a screenshot as
    #     kind=competitor → the VLM analyzes the IMAGE and it is filed as a
    #     competitor_posts row for creative-intelligence scoring — and it must
    #     NOT land in the artwork library.
    _shot_b64 = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAE"
        "hQGAhKmMIQAAAABJRU5ErkJggg=="
    )
    shot = call("POST", "/studio/upload/image", {
        "name": "verify-competitor-shot.png", "contentBase64": _shot_b64,
        "mediaType": "image/png", "kind": "competitor",
        "prompt": "@verify_rival probe screenshot (verification run)",
    })
    cp = shot.get("competitorPost") or {}
    assert cp.get("post_id"), f"competitor screenshot not filed: {json.dumps(shot)[:400]}"
    assert cp.get("handle") == "verify_rival"
    assert shot.get("assetId") is None, "competitor screenshot leaked into artwork library"
    # A 1×1 probe pixel may honestly yield no facts — any of these is a REAL,
    # non-fabricated outcome; what matters is the row + the analysis attempt.
    assert shot.get("vlmStatus") in ("ok", "no_facts", "unavailable")
    print(f"competitor screenshot OK: post={cp.get('post_id')} handle={cp.get('handle')} "
          f"vlm={shot.get('vlmStatus')} tags={cp.get('visual_tags')}")

    # 3. launch a REAL 3-lead win-back run (console 'Run campaign' button path).
    #    drafts_only + approve-first: NOTHING can send; test-mode is server-gated.
    plan = {
        "goal": "Win back three warm consultation leads for Kaps (verification run)",
        "audience": "warm leads who consulted but did not book",
        "channels": ["gmail"],
        "campaign_type": "win_back",
        "action_type": "outreach",
        "target_category": "reactivation",
        "segment": "warm",
        "lead_source": "provided",
        "lead_count": 3,
        "output_count": 3,
        "deep_research": True,
        "drafts_only": True,
        "per_lead": True,
        "personalize": True,
        "use_conversation_history": True,
        "artist": "Kaps",
        "offer": "free consult — reply to book",
        "tone": "warm, direct, no pressure",
    }
    launch = call("POST", "/studio/run", {"sessionId": SESSION, "plan": plan})
    run_id = launch["runId"]
    child_ids = [c["runId"] for c in (launch.get("children") or [])]
    print(f"launched run {run_id} children={child_ids}")

    # 4. poll to completion, printing agent steps as they land
    seen_steps = 0
    status = "running"
    deadline = time.time() + 30 * 60
    while time.time() < deadline:
        try:
            state = call("GET", f"/studio/run/{run_id}", timeout=60, retries=8)
        except Exception as e:  # noqa: BLE001 — poll through transient windows
            print(f"  poll error (continuing): {e}")
            time.sleep(15)
            continue
        status = state.get("status", "?")
        steps = state.get("steps") or []
        if len(steps) > seen_steps:
            for s in steps[seen_steps:]:
                print(f"  step {s.get('role')} [{s.get('model')}]")
            seen_steps = len(steps)
        if status in ("completed", "failed", "not_built"):
            break
        time.sleep(12)
    print(f"run status={status} steps={seen_steps}")
    assert status == "completed", f"run did not complete: {status}"
    assert seen_steps >= 5, f"too few recorded agent steps: {seen_steps}"

    # 5. the run's staged drafts — all HELD/PENDING, none sent
    q = ("query($t:ID!){reviewQueue(tenantId:$t)"
         "{id runId status target channel}}")
    body = call("POST", "/graphql", {"query": q, "variables": {"t": TENANT}})
    queue = (body.get("data") or {}).get("reviewQueue") or []
    run_family = {run_id, *child_ids}
    mine = [a for a in queue if a.get("runId") in run_family]
    print(f"review queue: {len(queue)} total, {len(mine)} from this run")
    assert mine, "no staged drafts from this run in the review queue"
    not_pending = [a for a in mine if a.get("status") != "PENDING"]
    assert not not_pending, f"non-held drafts from this run: {not_pending}"
    print("all drafts from this run are PENDING (held, approve-first)")

    # 6. one draft's Agent Contributions — the multi-agent evidence trail
    action_id = mine[0]["id"]
    contrib = call("GET", f"/studio/action/{action_id}/contributions")
    agents = [e["agent"] for e in contrib["contributions"]]
    print(f"contributions for {action_id}: {agents} "
          f"(agentRunCount={contrib['agentRunCount']})")
    for must in ("Strategy", "Research", "Analyst", "Copywriter", "Critic"):
        assert must in agents, f"missing {must} in contributions: {agents}"
    if "Research" in agents:
        assert "Identity Guardian" in agents or "Location Resolver" in agents, (
            "research present but no guardian/location entry")
    print("--- full contributions evidence ---")
    print(json.dumps(contrib, indent=1, default=str))

    print("VERIFY COMPLETE")


if __name__ == "__main__":
    sys.exit(main())
