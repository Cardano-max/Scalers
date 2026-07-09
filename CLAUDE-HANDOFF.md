# CLAUDE-HANDOFF — session continuity log

_Give this file to a fresh Claude session to continue exactly from here. Keep it updated at every meaningful step (the operator has asked for this explicitly). Last update: **2026-07-09 ~14:15 UTC** by the cloud session working on branch `claude/supervisor-state-recovery-4mp2vg` (PR #124)._

## 0. TL;DR
The whole product now runs from a fresh clone: `scripts/run-local.sh` (PG → initdb → `engine/bootstrap_db.py` → engine :8000 → console :3000). Real skindesign data is imported (1,092 customers, 37 artists, 5 real past campaigns as campaign memory + substantiated offers). Draft-count exactness, channel routing (email vs Instagram crews), VLM artwork upload → artist memory, top-4 artwork mid-run pause → operator choice → resume, live agent panel, research safety (protected-traits ban), delivery hardening (attachments, SMTP fallback, per-action IG media) are all built and were verified LIVE in this session. Delivery of real email is blocked only on credentials/egress (see `docs/CREDENTIALS-AND-EXTERNALS.md`, statuses verified). PR #124 (draft) carries everything; CI is being driven green; operator has ordered: **merge all PRs once green**.

## 1. Where things run (this cloud session)
- Postgres 16 **native** (not docker; docker registry blocked by egress policy): `postgresql://scalers:scalers@localhost:5432/scalers`, pgvector 0.6.0.
- Redis native :6379. MinIO NOT running (uploads store to disk under `engine/var/artifacts/` instead — by design of the new upload path).
- Engine: `cd engine && ENGINE_DATABASE_URL=postgresql://scalers:scalers@localhost:5432/scalers SCALERS_EMBEDDER=deterministic STUDIO_TENANT_ID=skindesign uv run uvicorn main:app --host 127.0.0.1 --port 8000`
- Console: `cd web && npm run dev -- --port 3000` (`.env.local` → tenant skindesign, backend :8000).
- Secrets: `engine/.env` (gitignored, chmod 600). Contains ANTHROPIC ✅, FIRECRAWL ✅, META (page perms missing), GMAIL client + DEAD refresh token, OPENAI ✅ (added 07-09), SMTP_SENDER/SMTP_APP_PASSWORD ✅ (added 07-09), GMAIL_REDIRECT_TO=operator inbox.

## 2. What was done this session (all verified live, not claimed)
1. **Fresh-clone fixes**: committed missing DDL (`infra/initdb/15-customers.sql`, `21-customer-personas.sql` — customers/customer_personas/tattoo_history existed only on the old dev machine); `engine/bootstrap_db.py` provisions all ~20 lazily-created store tables; `scripts/run-local.sh` one-shot bring-up.
2. **Real data imported** via `python -m studio.client_import .inbound skindesign` (1,092 customers, 37 artists; tenant forced test_mode=TRUE). PII stays in gitignored `.inbound/`.
3. **Campaign memory**: 5 real past campaigns (Christina $2,800 / Kirshten / Natalie $1,200 / Keebs $1,200 / Lynn) imported into `campaign_examples` (+10 extracted patterns) from operator screenshots → `.inbound/campaign-examples.json`.
4. **Real offers doc** uploaded (kind=offers): codes KEEBS ($1,200 full-day), CHRISTINA ($2,800), PAYPLAN (Klarna/Affirm) — drafts may now cite these; anything else is blocked by the substantiation gate.
5. **Draft-count exactness (spec §14)**: two root-cause fixes — (a) warm/past-customer framing now gates on real relationship evidence (was: profile-presence ⇒ every cold lead got "welcome back" copy ⇒ guard killed it ⇒ 3 asked/0 staged); (b) guard-killed drafts refill from the next contactable customer (bounded 2×, cohort path only). Live: 3→3, 5→5 with the real KEEBS offer.
6. **Landed 9 stranded origin branches**: nmh.7 (research agent), nmh.6 (dossier/campaign memory), nmh.9 (**channel routing** — conflicts resolved), nmh.11 (staging stability), tlv.6 (demo slice), bug/4hj, 65w14, 4jx13 (server-side hold), wwy.5.
7. **4 parallel builder agents** (worktrees, all merged):
   - ENGINE-CORE: image upload → disk (`var/artifacts/`) + VLM (citation-gated `ingest_vlm`, now actually wired) + artist link + artist memory row; read APIs `GET /studio/artists`, `/studio/artists/{slug}`, `/studio/artifacts`(+`/raw`), `POST /studio/artists/{slug}/memory`; artwork top-4 + `awaiting_selection` pause + `POST /studio/campaign/{run_id}/select-artwork` durable resume; supervisor live-state tools (chat host + voice `liveState`); stage_publish run_id lineage; IG pipeline depth (artist_memory + trend_research crew steps, Firecrawl-cited).
   - WEB-CONSOLE: Artists tab (roster/profile/gallery/upload+prompt/past campaigns/memory timeline), role-driven LIVE agent panel (no hardcoded pipeline; shows exactly the roles that ran), artwork picker modal, Runs tab states, 10-defect QA sweep, artifact library on Campaign memory.
   - RESEARCH-SAFETY: deterministic protected-traits ban (9 categories, prompt + post-filter + `trait_filtered` audit) across psych/research/social; consumer-shaped research queries; `research_depth=="deep"` gate; consent-safe social (customer-provided handles only).
   - DELIVERY: Gmail attachments (fail-closed), **SMTP fallback** (engages only on dead Gmail token, below the whole gate stack), per-action IG media + `PUBLIC_ASSET_BASE_URL`, `SCALERS_OUTPUT_HARD_CAP` (1..500), unified send_audit rows.
8. **E2E scenario verified live**: IG run = artist_memory→trend_research→researcher→strategist→draft→critic→jury crew, paused for artwork, resumed after choice; review queue 7 HELD items newest-first (2 IG posts + 5 emails, real recipients); Keebs profile page shows real past campaign (152 delivered/32 failed/50 DND) + 3 uploaded artworks + memory timeline; TEST-MODE server gate REFUSED a real-customer approve; allowlisted operator-inbox approve exercised Gmail(dead)→SMTP fallback→honest network error (this sandbox blocks TCP:465 — works on a normal machine).

## 3. Known truths / gotchas
- **Anthropic + Firecrawl work through this sandbox's proxy; SMTP (raw TCP), Google Drive, huggingface.co are blocked** by the environment egress policy.
- Gmail OAuth refresh token is EXPIRED/REVOKED (verified via token exchange). Re-consent (Option A) or run on a machine where SMTP egress works (Option B creds already in `.env`).
- Meta page token lacks page permissions (OAuthException 190/145) — see credentials doc §5.
- Engine model policy clamp (haiku default / sonnet-4-5 ceiling) is ACTIVE and must stay (CustomerAcq-8sk).
- Safety spine intact and re-verified: HELD/approve-first, server-side TEST-MODE tenant gate, no-fabrication guards, redirect-first sends. skindesign allowlist currently = the operator's own inbox only.
- Shared-DB test hazard: some integration tests write to whatever `ENGINE_DATABASE_URL` points at (they wiped some demo drafts once during this session). CI now provisions its own schema (ci.yml initdb+bootstrap step). Locally, prefer a scratch DB for full suite runs.
- The 3 uploaded Keebs artworks are clearly-labelled TEST placards (VLM honestly found no tattoo tags on them). Replace with real artwork uploads via the Artists tab.

## 4. In flight RIGHT NOW
- **PR #124** (draft, branch `claude/supervisor-state-recovery-4mp2vg` → main). CI was driven green across 5 rounds of fixes; every class was a fresh-clone/lane-hygiene issue, never product code: (1) ruff sweep; (2) integration lane ran on a bare DB → ci.yml now applies initdb + bootstrap_db.py; (3) four modules wrote ENGINE_DATABASE_URL into os.environ at import, un-skipping every skipif-guarded PG module in the DB-free unit lane → resolve-from-env; (4) smoke-gold-set autouse fixture no-ops without a DB; (5) done-gate: seed-dependent tests (hardcoded dev-machine cust ids / never-committed 60-customer seed) made self-seeding + by-email; the nmh.2 distinct-goals test re-pinned to the nmh.11 one-pending-per-recipient DDL contract (SEMANTIC DECISION: nmh.11's phantom-duplicate fix supersedes both-goals-land; to re-target a recipient, resolve their pending draft first).
- Operator's standing orders: (a) **merge all PRs** once CI is green (main is branch-protected: 5 required checks incl. done-gate, enforce_admins on); (b) keep THIS handoff file updated; (c) operator will supply Meta page token + real artwork images "in a while". OPENAI + SMTP creds received 07-09 and installed in engine/.env.

## 4b. MERGE OUTCOME (2026-07-09 ~15:15 UTC)
- **PR #124 MERGED to main** (merge commit 1d912a3) after 5 green checks incl. done-gate. The merge-commit method carried all constituent branch tips into main's history, auto-resolving 14 open PRs.
- **#111 (wwy.5 gold prune) MERGED** separately.
- **#94 (65w15) + #76 (a9m9 fixtures) CLOSED as superseded** (content already on main / base branch never merged) — comments on each explain.
- **#107 (fr1.4 RLS hardening) is the ONE remaining open PR**: retargeted to main, conflicts need a real resolution pass + a non-superuser role for the local bring-up before it can land (comment on the PR has details).
- Meta user token received (7 perms incl. instagram_content_publish) and stored in engine/.env; graph.facebook.com is BLOCKED from this sandbox, so run `python3 scripts/meta_mint_page_token.py` ON THE OPERATOR'S MACHINE within ~1h of token mint to exchange + save the permanent PAGE token / IG business id. api.openai.com is likewise blocked here — voice mint verified only as far as the egress wall; test voice locally.

## 5. Next steps (in order)
1. DONE — #124 merged; see §4b.
2. After merge: re-point the local stack at main, re-run `scripts/run-local.sh` sanity.
3. When the operator provides real artwork images → upload via Artists tab → verify VLM tags are real (style/motif/color) → re-run the artwork-pause scenario with real tags matching (e.g. "lion linework" query should rank the lion piece first).
4. When Meta page token with perms arrives → set in `engine/.env` → test IG publish to the studio's own account (app-tester scope), using `PUBLIC_ASSET_BASE_URL` or tunnel for media.
5. Real email delivery proof on the operator's machine (SMTP) or after Gmail re-consent (works from anywhere incl. this sandbox since it's HTTPS).
6. Voice supervisor: OPENAI_API_KEY is now set — mint `/studio/voice/session` and test the speaking flow (may need OpenAI credits if 429).
7. Remaining spec items not yet built: Facebook-specific pipeline variant (§11 FB tone), Drive/Instagram artwork import (blocked here; buttons are honest stubs), campaign outcome/performance feedback loop (§5 "what worked"), 500-lead durable cohort executor (tlv.1 branch — major conflicts, deferred), reply/outcome capture memory (tlv.2 — failed QA earlier, deferred).

## 6. Key artifacts
- `docs/CREDENTIALS-AND-EXTERNALS.md` — verified credential statuses + how-to-get.
- `scripts/run-local.sh`, `engine/bootstrap_db.py` — bring-up.
- `.inbound/` (gitignored) — real PII CSVs + campaign transcription JSON.
- Screenshots of the finished UI: scratchpad `final-shots/` (artists-roster, keebs-profile, review-queue).
- PR #124: https://github.com/Cardano-max/Scalers/pull/124
