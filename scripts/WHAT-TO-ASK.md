# WHAT TO ASK — meeting demo sheet (current verified flow)

Run `scripts\update-stack.ps1` first. It pulls latest, backfills artwork tags,
restarts, and self-verifies. Console opens at **http://localhost:3005**.

Golden rule to say out loud early: **"Every number on screen is fetched from our
database or Meta's API — the language models only write words, they never invent a
fact. And the safety gate is in the database, not the prompt."**

---

## 0 — Before you screen-share (2 min)
1. `scripts\update-stack.ps1` → wait for green **READY FOR THE MEETING**. Note it prints `artwork tagged: N pieces` — N must be > 0.
2. Two tabs open as proof:
   - `localhost:8000/healthz` → `modelKeyPresent: true`, `studioTenant: skindesign`
   - `localhost:8000/studio/meta/verify` → your Meta status (publish stays gated either way)
3. Console → **+ New session** (fresh thread so no old run is bound).
4. Confirm the **SAFE MODE** banner is up.

---

## 1 — The one command, three channels (the headline)
On the **Voice** tab, type OR say this **one** brief (voice and text are equal):

> **"I want a campaign across email, Instagram and Facebook. For email: win back
> three Keebs customers who stepped back over price or timing — from the imported
> conversation threads, use their real conversations, exactly three drafts, deep
> research on, offer is the twelve-hundred-dollar full-day session with payment
> plans. For Instagram: promote Keebs' fine-line botanical work — research
> competitors first and attach fine-line botanical images. For Facebook: a page
> post about Keebs' full-day sessions with payment plans."**

Then, when it asks to confirm the three leads or the plan:

> **"Yes, go ahead and run it now."**

**What you should SEE (and say):**
- Host replies with the 3 leads + the plan, then **"Multi-channel campaign LIVE — three isolated runs"** with three run ids ending `-email`, `-ig`, `-fb`.
- Move to the **Agency** tab. The panel binds to your run itself (you don't attach anything) and shows **the agents landing live** — each row is a real agent with its real model and output: `researcher → analyst → copywriter → critic → jury` on email; the social legs add `competitor_intel` and `molder`. Say: *"This isn't a spinner — each row is an agent that actually ran, with its output."*

---

## 2 — Competitor popup (the star moment)
The Instagram (and Facebook) leg pauses and a **competitor picker** opens, labelled
with which channel is asking.

- It shows **up to 10 real competitor posts, scored**, best-first. Each card: real
  caption, real like/comment/follower counts, a computed **score** and the honest
  "why it worked" breakdown (e.g. *niche_match 10/10, style_match 10/10; no data for
  engagement_rate → excluded, not assumed*).
- Say: *"Ten competitors, scored against THIS brief — botanical work ranks top, an
  off-theme high-engagement post ranks lower. Deterministic, not vibes."*
- **Click the top one.** The run resumes and the **molder** rewrites that winning
  post's SHAPE into our brand voice — never a copied sentence.

---

## 3 — Artwork popup (4 images, pick one)
Each social leg then pauses on a **4-image artwork picker**, labelled by channel.

- The four are **OUR own pieces, ranked to the brief** — a fine-line botanical brief
  surfaces the Dahlia / sunflower / botanical pieces at the top (not a random
  heavily-tagged piece). Each card shows the reason it was matched.
- Say: *"It matched the winning look against our own library's VLM tags — these four
  are ours, botanical-first because the brief was botanical."*
- **Click the top one.** That exact image attaches to the post.

*(Repeat competitor-pick + artwork-pick for the other social leg when it asks.)*

---

## 4 — Review queue (the "wow")
Go to **Review queue**. Each social post renders as a real feed card:

- The **actual image** you picked (served from our library)
- The **hook** on top, the caption that **describes that exact piece**
- **angle · CTA · keyword** chips (the CTA matches the caption's real ask)
- **"molded from @competitor · structure hook → context → cta · shape only, never
  copied"** — with the score + a link to open the original and compare

Click an **email** draft too: full evidence chain — which conversation was read, the
analyst's classification quoting the customer's OWN words, the critic verdict, jury
confidence. Everything is **HELD** — approve stages it for the (still-gated) publish
step; nothing sends.

---

## 5 — Break it (prove the safety spine)
1. On a publishable IG post, click **Approve** → server refuses: *"TEST MODE — real
   customer sends disabled."* That's a **database** tenant flag, not a prompt.
2. Type: *"Send Kassie Wesley an SMS about the Keebs special."* → refused: her
   **consent flag** outranks the operator.
3. Say: *"Consent first, then server gates, then me. I'm third in my own system —
   that's what makes autonomy safe."*

---

## 6 — Close (30 sec)
*"One spoken command became three isolated pipelines. Instagram scored ten real
competitors, molded the winner's shape into our voice without copying a word, and
attached our own botanical piece. Email answered three real customers' actual
objections in their own words. Every number was fetched, the guardrails live in the
database, and the system refused me three times. Go-live is one flag flip."*

---

## FALLBACKS (if something stalls)
- **Host asks to confirm leads/plan instead of launching:** just say *"Yes, use your
  best three warm leads, go ahead and launch all three channels now."*
- **Competitor popup takes 30–60s:** *"It's live — public search then Meta's API.
  Real data takes a second; fabricated data is instant. The wait is the proof."*
- **A second popup after you pick:** ig and fb each raise their own competitor +
  artwork pause, one after another — the label on the popup tells you which leg.
- **Panel says "agency is ready" for a few seconds:** give it ~4s, it auto-binds; or
  the Agency tab will show the same run.
- **An image shows a grey placeholder:** refresh once (a first-load race can latch);
  it renders on reload.
- **SMS/consent skip appears:** that's the Act-5 guardrail, not a bug.

## ONE-LINERS to repeat
- "Fetched, not generated."
- "Molded, never copied."
- "Guardrail's in the database, not the prompt."
- "I'm third in my own system — consent, then server, then me."
