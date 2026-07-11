"""Grade a finished campaign the way the CLIENT will, not the way the engine reports itself.

The engine is perfectly capable of saying "completed" over a post with no image, a caption
molded from nothing, and a leg that silently staged nothing at all — every one of those
shipped. So this asserts the OUTCOME on the staged rows, and the assertions are the ones an
operator would make with the review queue open:

    uv run python scripts/redteam_grade.py <run_id> --ig-must dahlia --fb-must blackwork
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import psycopg

DSN = os.environ.get("ENGINE_DATABASE_URL", "postgresql://scalers:scalers@127.0.0.1:5432/scalers")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_id")
    ap.add_argument("--ig-must", default=None, help="tag the IG image MUST carry (the brief)")
    ap.add_argument("--fb-must", default=None, help="tag the FB image MUST carry (the brief)")
    ap.add_argument("--tenant", default=os.environ.get("STUDIO_TENANT_ID", "skindesign"))
    args = ap.parse_args()

    checks: list[tuple[bool, str]] = []

    def check(ok: bool, label: str) -> None:
        checks.append((bool(ok), label))

    with psycopg.connect(DSN, autocommit=True) as conn:
        rows = conn.execute(
            "SELECT run_id, channel, type, subject, draft, context FROM actions "
            "WHERE run_id LIKE %s AND status='pending'",
            (f"{args.run_id}%",),
        ).fetchall()
        legs = {
            r[0].rsplit("-", 1)[-1]
            for r in conn.execute(
                "SELECT DISTINCT run_id FROM agent_runs WHERE run_id LIKE %s",
                (f"{args.run_id}-%",),
            ).fetchall()
        }

    posts = {r[1]: r for r in rows if r[2] == "post"}
    emails = [r for r in rows if r[2] != "post"]

    # ---- every leg that ran must have STAGED something -------------------------------
    for leg in ("email", "ig", "fb"):
        check(leg in legs, f"{leg} leg ran at all")
    check(len(emails) == 3, f"email staged 3 per-lead drafts (got {len(emails)})")
    check("ig" in posts, "instagram staged a post")
    check("fb" in posts, "facebook staged a post")

    # ---- each post must be a POST: image + caption + CTA + hashtags + the mold --------
    for ch, must in (("ig", args.ig_must), ("fb", args.fb_must)):
        row = posts.get(ch)
        if not row:
            continue
        ctx = json.loads(row[5]) if row[5] else {}
        art = (ctx.get("artwork") or {})
        comp = (ctx.get("competitor") or {})
        tags = ctx.get("hashtags") or []
        vlm = (art.get("vlmSummary") or "").lower()

        check(bool(art.get("artifactId")), f"{ch}: the post carries a real IMAGE")
        check(bool(row[4] or row[3]), f"{ch}: the post carries a caption")
        check(bool(ctx.get("cta")), f"{ch}: the post carries a CTA")
        check(len(tags) > 0, f"{ch}: the post carries grounded hashtags ({len(tags)})")
        check(bool(comp.get("handle")), f"{ch}: the operator's competitor pick is ON the draft")
        check(
            comp.get("totalScore") is not None and bool(comp.get("whyItWorked")),
            f"{ch}: the mold records its real score + why it worked",
        )
        if must:
            check(must.lower() in vlm, f"{ch}: the image ANSWERS the brief ('{must}')")

    # ---- the emails must be real, named, per-lead ------------------------------------
    named = [e for e in emails if e[3] and len(e[3]) > 8]
    check(len(named) == len(emails) and emails, "every email has a real subject line")
    check(len({e[3] for e in emails}) == len(emails), "the emails are distinct, not one blast")

    passed = sum(1 for ok, _ in checks if ok)
    print()
    for ok, label in checks:
        print(("  PASS  " if ok else "  FAIL  ") + label)
    print(f"\n  {passed}/{len(checks)} checks passed")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
