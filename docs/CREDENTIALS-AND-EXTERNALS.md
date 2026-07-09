# Credentials & external dependencies — what we need, why, and how to get it

_Verified live on 2026-07-09 against the running stack (engine :8000, console :3000, Postgres 5432). Status is what the system actually reported, not an assumption. All secrets go into the gitignored `engine/.env` — never commit them, never paste them in chat/screenshots._

| # | Credential / dependency | Status (verified) | Blocks what |
|---|---|---|---|
| 1 | `ANTHROPIC_API_KEY` | ✅ WORKING | nothing — drafting, jury, psych analysis, VLM all live |
| 2 | `FIRECRAWL_API_KEY` | ✅ WORKING | nothing — web/trend research live |
| 3 | Gmail OAuth refresh token | ❌ EXPIRED/REVOKED (`invalid_grant`, tested via token exchange, no email sent) | real email delivery (drafting/queue/approve all work; the final send fails) |
| 4 | SMTP app password (`SMTP_SENDER` + `SMTP_APP_PASSWORD`) | ✅ PROVIDED (2026-07-09) — fallback code engages correctly, but THIS cloud container blocks raw TCP:465 egress, so the actual delivery could only fail here with a concrete network error. On the operator's own machine it will deliver. | nothing on your machine; blocked only in this sandbox |
| 5 | Meta page token with page permissions | ❌ INSUFFICIENT PERMISSIONS (OAuthException 190/145: needs `pages_read_engagement` / `pages_manage_metadata` / `pages_read_user_content` / `pages_show_list`) | Instagram + Facebook publishing |
| 6 | `OPENAI_API_KEY` | ✅ PROVIDED (2026-07-09), in `engine/.env` | voice supervisor unblocked (realtime tier still needs OpenAI credits if 429s appear) |
| 7 | Google Drive artwork folder | ❌ UNREACHABLE from this cloud environment (network policy blocks drive.google.com) | bulk artwork import — workaround: upload images in the console (Artists tab) |
| 8 | huggingface.co (fastembed model download) | ❌ BLOCKED by this environment's network policy | real semantic embeddings — the engine runs with the documented offline `SCALERS_EMBEDDER=deterministic` stub until then |

---

## 3. Gmail delivery — pick ONE of two options

### Option A — re-consent the OAuth token (the "proper" path)
Why: the engine's Gmail connector sends via the Gmail API using a refresh token. Google expires refresh tokens after ~7 days while the OAuth app is in "Testing" mode, which is why it keeps dying.

How:
1. Go to https://console.cloud.google.com → your project → **APIs & Services → OAuth consent screen**.
   - Either click **Publish app** (stops the 7-day expiry permanently), or make sure your Google account is listed under **Test users**.
2. **Credentials** → your OAuth client (the `GMAIL_CLIENT_ID` already in `.env`) → make sure `http://localhost:8765/` is in **Authorized redirect URIs**.
3. On the machine that runs the engine, run the helper that already exists for this: it opens a listener on :8765 and prints a Google consent URL (scope `gmail.send`). Log in with the sender account, click Allow.
4. It writes the new `GMAIL_REFRESH_TOKEN` into your env file. Restart the engine.

### Option B — SMTP app password (simpler, what you chose earlier)
Why: bypasses OAuth entirely; the engine sends over SMTP-SSL with an app password. Survives indefinitely.

How (5 minutes, on the Google account that will send):
1. https://myaccount.google.com/security → enable **2-Step Verification** (required for app passwords).
2. https://myaccount.google.com/apppasswords → create an app password (app: "Mail").
3. Add to `engine/.env`:
   ```
   SMTP_SENDER=yoursender@gmail.com
   SMTP_APP_PASSWORD=<the 16-char app password>
   ```
4. Restart the engine. The publish path uses Gmail API when its token works and falls back to SMTP automatically; TEST-MODE / redirect / approval gates apply identically.

> Safety note: the server-side TEST MODE gate for tenant `skindesign` refuses real-customer sends regardless of which option you pick. First live test should use `GMAIL_REDIRECT_TO=<your own inbox>` so the send provably lands somewhere safe.

## 5. Meta (Instagram + Facebook publishing)

Why: publishing needs a **Page access token** that carries page permissions, and IG content publishing for non-testers needs Meta **App Review** approval of `instagram_content_publish`.

How:
1. https://developers.facebook.com → your app → **Graph API Explorer**:
   - Select your app → "Get Page Access Token" → grant: `pages_read_engagement`, `pages_manage_metadata`, `pages_read_user_content`, `pages_show_list`, `pages_manage_posts`, `instagram_basic`, `instagram_content_publish`.
2. Exchange for a **long-lived** page token (Graph explorer or the token debugger) and put it in `engine/.env` as the page token the connector reads.
3. Make sure the IG business account is linked to that Facebook Page (Meta Business Suite → Instagram → connected assets) and `IG_BUSINESS_ACCOUNT_ID` matches.
4. For use beyond app testers/admins: submit **App Review** for `instagram_content_publish` (screencast + description) and start **Business Verification** — that is the days-long piece, everything else is same-day. Until review passes, publishing works for accounts with a role on the app (admin/developer/tester) — good enough for the studio's own accounts if they're added as testers.
5. IG API requires a **publicly reachable image URL** for media. Locally that means either the Cloudflare tunnel in front of the console (`PUBLIC_ASSET_BASE_URL=https://<tunnel-host>`) or any public URL for the image.

## 6. OpenAI (voice supervisor only)

Why: the speaking supervisor uses the OpenAI Realtime API for speech. Text chat + the whole pipeline run fine without it.

How: https://platform.openai.com → create API key → add credits (the realtime tier 429s on an unfunded account) → `OPENAI_API_KEY=...` in `engine/.env`.

## 7–8. This cloud environment's network policy

This session runs in a sandboxed cloud container whose egress policy currently blocks `drive.google.com` and `huggingface.co` (both returned connection failures, verified). On YOUR machine none of this applies. If you want them reachable here, they need to be added to the environment's allowed domains (Claude Code environment settings → network policy). Until then:
- Artwork: upload images directly in the console (Artists tab → Upload artwork) — the VLM analysis runs on upload either way.
- Embeddings: the engine runs with the honest offline embedder (`SCALERS_EMBEDDER=deterministic`); switch to fastembed once huggingface is reachable (just unset the env var and restart).
