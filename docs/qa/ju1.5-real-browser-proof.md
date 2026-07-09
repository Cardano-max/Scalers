# ju1.5 — REAL-BROWSER proof (operator done-bar)

Driven 2026-07-02 in real Chrome against the real stack: engine `uvicorn main:app`
on :8010 with `ENGINE_DATABASE_URL` set (durable/live), Next dev on :3031, local
Postgres with the REAL ju1.1/ju1.2 data (skindesign tenant `test_mode=true`,
1092 imported customers, 37 artists, 5 transcribed campaign examples). Two demo
drafts were staged through the engine's own `record_pending_action` with
dossier-shaped context grounded in real imported customers (run
`team-camp_ju15demo-086f39e2`; they remain HELD/pending for the ju1.6 acceptance run).

Screenshots captured in-session (Claude-in-Chrome IDs, embedded in the bead
session transcript):

| # | ID | Proves |
|---|----|--------|
| 1 | ss_1869n9736 | Landing: server-driven **TEST MODE banner** ("Real customer sends disabled for Skin Design Tattoo — approvals stage drafts as HELD; the server refuses any live send (allowlist: 0 operator addresses)"), default tenant **Skin Design Tattoo** in the switcher, **Campaign memory** nav item, review badge 2 |
| 2 | ss_51859di0f | Review queue: per-draft **TEST MODE chips** (rows + detail header), **full lineage panel** for the SMS draft — source file `customers.csv`, customer name/email/phone, honest-missing artist ("customer has no artist on file"), studio, campaign example ("per-draft example provenance lands with ju1.4"), offer; CTA "reply YES to claim"; channel `sms` + **"no SMS send path yet"** chip; limited-personalization note; Live toggle disabled with **"server-enforced"** reason |
| 3 | ss_9820o6hde | Gmail draft lineage with GROUNDED fields: offer **FLASH1200** (parsed from the dossier angle source), CTA "reply YES to hold your spot", customer Denise Ruiz + email + phone, source `customers.csv` |
| 4 | ss_8280srlwn / ss_37412sf95 | Draft detail: evidence panel + dossier context + Approve & send / Reject controls (approve stays enabled — stages HELD) |
| 5 | ss_59350skct | **Campaign memory**: real examples ("06.18 Angel Mini App + Rev $1200" — artist/offer/994 recipients/661 delivered/127 failed/186 DND/CTA + full message copy + `OPERATOR_SCREENSHOT` provenance badge) with the **actual source screenshots streamed from the local client-data dir** |
| 6 | ss_6260ah8b0 | Tenant switched to **Ladies First (dev fixture)**: **NO banner** (unregistered tenant), its own data (review badge 311), memory honest-empty — the edge-case AC |

## Server refusal (AC3) — proven on the wire

Clicking **Approve & send** on the gmail draft fired the real GraphQL
`approveAction` → `approve_and_publish` → ju1.1 `check_send_allowed` gate.
DB ground truth immediately after the click:

```
act_0d2213a9798b430d | pending | TEST MODE - real customer sends disabled for
tenant 'skindesign'; recipient 'deniseruiz143@gmail.com' is not on the
operator-approved test allowlist
```

Status stayed `pending` (re-approvable, nothing sent, no side-effect row) —
the UI disable is a courtesy; the SERVER is the defense, exactly per the AC.

## Endpoint proofs (same-origin through the Next proxy)

- `GET /tenants/skindesign` → `{"registered":true,"name":"Skin Design Tattoo","testMode":true,...}`
- `GET /studio/campaign-examples?tenant_id=skindesign` → the 5 real examples
- `GET /studio/campaign-examples/cex_ea3b72a3785ce321/screenshot` → 200 `image/jpeg` (local file)
- `GET /studio/action/act_0d2213a9798b430d/lineage` → full lineage JSON with honest nulls
  for artist/studio (skindesign customers carry no artist column — real data state)
