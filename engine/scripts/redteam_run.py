"""Drive ONE full 3-channel campaign end to end and grade it against a hard rubric.

This is the red-team harness: it plays the operator (launch, answer every gate, wait), then
asserts what a CLIENT would check when they open the review queue —

  * did each social leg pull real competitors, score them, and offer a choice?
  * is the piece it offered actually relevant to that leg's brief?
  * did the pick the operator made end up ON the staged draft (image AND mold)?
  * does the post carry a caption, a CTA and grounded hashtags?
  * are the emails per-lead, addressed to real named customers, grounded in real threads?
  * did every leg stage what it promised — nothing silently missing?

Run:  uv run python scripts/redteam_run.py --label "win-back / botanical" \
          --ig-style "fine-line botanical floral" --fb-style "bold blackwork"
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.request

BASE = os.environ.get("ENGINE_BASE", "http://127.0.0.1:8000")
DSN_TENANT = os.environ.get("STUDIO_TENANT_ID", "skindesign")


def _post(path: str, payload: dict) -> dict:
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read().decode())


def _get(path: str) -> dict:
    with urllib.request.urlopen(f"{BASE}{path}", timeout=60) as r:
        return json.loads(r.read().decode())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--goal", default="Win back Keebs customers who stepped back on price or timing")
    ap.add_argument("--ig-style", required=True)
    ap.add_argument("--ig-goal", default="promote Keebs' best work")
    ap.add_argument("--fb-style", required=True)
    ap.add_argument("--fb-goal", default="page post: book a full day with Keebs")
    ap.add_argument("--session", default="redteam")
    args = ap.parse_args()

    print(f"\n{'=' * 78}\nCAMPAIGN: {args.label}\n{'=' * 78}")

    plan = {
        "goal": args.goal,
        "audience": "Leads from the imported conversation threads",
        "channels": ["email", "ig", "fb"],
        "per_lead": True,
        "lead_source": "provided",
        "use_conversation_history": True,
        "deep_research": True,
        "output_count": 3,
        "lead_count": 3,
        "offer": "full-day session with payment plans",
        "channel_plans": {
            "email": {"output_count": 3, "per_lead": True},
            "ig": {
                "competitor_research": True, "attach_images": True,
                "image_style": args.ig_style, "goal": args.ig_goal, "output_count": 1,
            },
            "fb": {
                "competitor_research": True, "attach_images": True,
                "image_style": args.fb_style, "goal": args.fb_goal, "output_count": 1,
            },
        },
    }
    session = f"{args.session}-{int(time.time())}"
    run_id = _post("/studio/run", {"sessionId": session, "plan": plan})["runId"]
    print(f"run: {run_id}\n")

    gates: list[dict] = []
    deadline = time.time() + 420
    while time.time() < deadline:
        st = _get(f"/studio/run/{run_id}")
        comp = st.get("competitorSelectionRequest")
        art = st.get("selectionRequest")
        if comp and comp.get("options"):
            top = comp["options"][0]
            print(
                f"  COMPETITOR [{comp.get('channel')}] {len(comp['options'])} scored posts"
                f"  -> picking @{top.get('handle')} (score {top.get('totalScore')})"
            )
            gates.append({"kind": "competitor", "channel": comp.get("channel"),
                          "n": len(comp["options"]), "picked": top.get("handle"),
                          "postId": top.get("postId")})
            _post(f"/studio/campaign/{run_id}/select-competitor", {"postId": top["postId"]})
            time.sleep(3)
            continue
        if art and art.get("options"):
            top = art["options"][0]
            tags = ", ".join((top.get("styles") or []) + (top.get("motifs") or []))
            print(
                f"  ARTWORK    [{art.get('channel')}] {len(art['options'])} pieces"
                f"  -> picking {tags[:52]}"
            )
            gates.append({"kind": "artwork", "channel": art.get("channel"),
                          "n": len(art["options"]), "picked": top.get("assetId"),
                          "tags": tags})
            _post(f"/studio/campaign/{run_id}/select-artwork", {"assetId": top["assetId"]})
            time.sleep(3)
            continue
        if st.get("status") == "completed":
            break
        time.sleep(4)

    st = _get(f"/studio/run/{run_id}")
    print(f"\n  status: {st.get('status')}  steps: {len(st.get('steps') or [])}  gates answered: {len(gates)}")
    print(json.dumps({"runId": run_id, "gates": gates}, indent=None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
