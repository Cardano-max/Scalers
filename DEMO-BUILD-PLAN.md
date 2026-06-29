# Live demo build — generate → approve → real send, in the live console

**Goal (operator, client demo today):** the designed Scalers Operator Console,
running LIVE on real Postgres data, where the operator reviews a generated action,
clicks Approve, and a REAL outreach email sends (Gmail) — visible in the console.
FB + IG wired the same way, gated on fresh Meta tokens (operator must re-mint).

**Hard gates (do NOT relax):** autonomy stays HOLD; every send is human-approved
in the console; no fake-pass, no green theater. Real means traced to a real API
response, not a replay.

## Architecture (2 services)

- **Backend** — the engine FastAPI (`engine/`, runs on `:8000`). Adds:
  (1) a GraphQL + SSE obs-API serving the console, (2) real connectors + an
  approve→publish path, all in-process against Postgres
  `postgresql://scalers:scalers@localhost:5432/scalers`.
- **Frontend** — the Next.js console (`web/`, `:3000`). Flip to live with
  `NEXT_PUBLIC_DATA_SOURCE=live`, `NEXT_PUBLIC_GRAPHQL_URL=http://localhost:8000/graphql`,
  `NEXT_PUBLIC_SSE_URL=http://localhost:8000/sse/stream`, `NEXT_PUBLIC_TENANT_ID=ladies8391`.

## The contract (authoritative — build to these, do not invent shapes)

- GraphQL operations the console SENDS: `web/lib/data/queries.ts`.
- TypeScript shapes the console EXPECTS: `web/lib/data/models.ts`.
- The published SDL: `eng1/src/gateway/OBS-CONTRACT.md` (reference).
- The review-queue read model: the `actions` table (`infra/initdb/08-actions.sql`).
  Jury card + confidence + gates come from `autonomy_decisions` + `autonomy_jury`
  joined by `decision_id` (NOT duplicated on `actions`).

## Python seams (between the api module and the actions/connectors module)

```
actions.store.record_pending_action(*, tenant_id, decision_id, type, channel,
    worker, target, draft, subject=None, context=None, conf, threshold,
    esc_kind, esc_label, idempotency_key) -> action_id
actions.store.list_actions(tenant_id, status=None) -> list[ActionRow]
actions.store.get_action(action_id) -> ActionRow | None
actions.publish.approve_and_publish(action_id) -> ActionRow   # marks approved,
    # sends via the right connector (gmail real; fb/ig gated), flips sent/failed,
    # writes deep_link. Exactly-once on idempotency_key.
actions.publish.reject(action_id) -> ActionRow
```

The GraphQL `approveAction`/`rejectAction` mutations call these.

## Components (parallel build)

- **A — backend obs-API** (`engine/obsapi/`, mounts in `engine/main.py`):
  strawberry GraphQL `/graphql` + SSE `/sse/stream` resolving every op in
  `web/lib/data/queries.ts` over PG (reviewQueue=actions where pending; action=
  join to decisions+jury; runs; feed; kpis/overview; systemHealth). CORS for :3000.
  Mutations delegate to the seams above.
- **B — console screens** (`web/`): Review queue (master/detail: jury card,
  confidence bar w/ threshold tick, per-dim jury bars, gate chips, Approve/Reject/
  Regenerate) + Activity, to the design (`frontend-handoff .../design_handoff_operator_console/README.md`).
  Live-mode wiring. Reuse the existing typed adapter + models.
- **C — connectors + actions + approve** (`engine/`): real `GmailConnector`
  (`engine/connectors/gmail.py`, refresh→access→users.messages.send) + the
  `actions` store/publish modules + `record_pending_action` from the decision
  path. FB connector exists (`engine/connectors/fb.py`); IG/FB gated on valid token.

## Credential reality (verified live 2026-06-29)

- Gmail refresh token: VALID, scope `gmail.send`. Real send works. Read from
  `.finalenv.txt` at runtime (never commit secret values).
- FB page token: EXPIRED + missing `pages_manage_posts`. IG token: invalid.
  → operator must re-mint a long-lived page token. FB/IG fire once valid.

## Status
- [x] build branch `demo/live-vertical-slice` off origin/main (eng5/src)
- [x] Postgres + schema up; `actions` table applied
- [ ] A backend obs-API  [ ] B console screens  [ ] C connectors+approve
- [ ] integration: live stack up, real decision/action seeded for ladies8391
- [ ] real Gmail send on approve, visible in console
