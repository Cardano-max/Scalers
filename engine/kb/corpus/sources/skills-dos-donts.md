# Skills-Layer DO's & DON'Ts — bake into the orchestrator / CLAUDE.md

Distilled from the practitioner Reddit threads (verbatim quotes in `winning-strategies-kb.md`)
and the read-only security vetting of the curated skill sources. Two flavors below:
(A) practitioner workflow rules, (B) supply-chain / adoption rules from the vet.

---

## DO

### Practitioner workflow (from the threads)
- Lead with brand-voice + the AI-flagger/humanize validator. Everything that writes depends on them — adopt these two first. ["Most teams skip that step and publish content that reads like a bot wrote it." — No_Trust_645]
- Start from the artist's/company's ACTUAL voice and positioning, never a generic SaaS voice. Load positioning, personas, messaging pillars, approved claims, product details, and high-performing examples into the per-tenant pack. [emilyxhug]
- Give every agent a clear role + clean context before it writes. "Prompt structure beats the tool." Generic inputs = generic results. [sapindia1976, tenegoacademy]
- Codify repeated work: "If I explain the same workflow twice, it becomes a skill or script. If I paste the same context twice, it belongs in the workspace." [kaancata]
- Keep a per-tenant current-state file + decision log; update it from each new signal so the model reads the business as it exists today, not whatever gets pasted. [kaancata]
- Treat the model as a thinking/workflow partner and a second set of eyes — use the back-and-forth; the first response usually isn't the best one. [Appropriate-Sir-3264, AccordingWeight6019, crawlpatterns]
- For structural problems (funnel leakage, pacing), ask it to ANALYZE friction points, not just rewrite sentences. [tenegoacademy]
- When critiquing, supply strategy/tactics/goals so "wrong" is contextualized, and rank findings low/medium/high. [xRyozuo]
- Feed strong references for any design/visual work to avoid long course-correction. [mathchew88]
- Build small skills and orchestrate them in a pipeline (Cowork-style) — skills are step one, the automations/pipelines are the edge. [BennyBingBong, CommissionDry8792]

### Adoption / supply-chain (from the vet)
- Prefer first-party Anthropic where it fits: brand-guidelines as the voice-layer anchor, skill-creator as the authoring/eval harness.
- Prefer prompt-only (markdown SKILL.md) skills. When adopting, vendor ONLY the `skills/*/SKILL.md` + `references/*.md`; strip every bundled CLI/script/GitHub-Action.
- Pin the exact upstream skill name + commit SHA on adoption (the doc's alias names like map-your-market / human-tone do not always exist verbatim upstream — provenance must be auditable).
- Re-author every adopted skill into our Agent Skills format with our brand-voice + determinism rules, then gate through skill-creator + eval gold-sets before any agent uses it.
- Build the AI-tells/humanize check as a DETERMINISTIC in-house validator (em-dash, rule-of-three, contrast framing, generic transitions) — no off-the-shelf classifier exists; seed from human-tone + copy-editing/plain-english-alternatives.md.
- Add a LICENSE file before redistributing anything from louisblythe/Sales-Skills (the fork ships no LICENSE; MIT carries by lineage only).
- Keep the human approval gate on outreach: suppression-first, capped + personalized sequences, all sends gated through our harness (CAN-SPAM/GDPR) — never auto-send.

---

## DON'T

### Practitioner workflow (from the threads)
- Don't ship anything unreviewed. "Nothing before review." / "everything needs review." [avanii_21, Informal_Cash_528]
- Don't treat it like a magic button — it's a second set of eyes, not a vending machine. [AccordingWeight6019]
- Don't feed it generic inputs and expect non-generic output. [tenegoacademy]
- Don't expect the first output to be the best; iterate. [crawlpatterns]
- Don't skip the AI-tell cleanup step before publishing. [No_Trust_645]
- Don't stop at collecting skills — without pipelines/automations they're just step one. [BennyBingBong]
- Don't ask "what's wrong" open-endedly with no context — it will always invent something. [xRyozuo]
- Don't expect end-to-end delivery on complex research — use it to avoid starting from zero, then review. [Relevant-Contest-919]

### Adoption / supply-chain (from the vet)
- Don't blind-install or auto-execute any third-party skill or its bundled scripts. Read the SKILL.md and every script first.
- Don't run scripts that ship with TLS verification disabled (brand-alchemy/domain_checker.py, map-your-market & where-your-customer-lives /fetch.py use `ssl._create_unverified_context()` / `CERT_NONE`) — if ever run, sandbox with verification restored.
- Don't enable coreyhaines31/marketingskills' 67 bundled Node CLIs (apollo/zoominfo/clearbit/hunter, resend/sendgrid/postmark email-SEND) by default — they read env API tokens, hit data brokers, and send real email. Opt-in, scoped creds, --dry-run only.
- Don't install the `ads` / `ad-creative` skills' `node tools/clis/google-ads.js` path without scoped ad-account creds — it claims direct ad-account write access.
- Don't install growthenginenowoslawski/coldoutboundskills — its .ts scripts SPEND REAL MONEY (Dynadot domain bulk-purchase) and create live Instantly/Smartlead campaigns. Mine patterns only.
- Don't adopt Nuwa Skill Distiller (skillhub.club/lobehub) — unclear/no OSS license, off-GitHub, auto-generates executable SKILL.md (generation + prompt-injection surface). Use first-party skill-creator instead.
- Don't trust a "first-party Anthropic marketing plugin" — none exists. The "marketing" plugin on third-party hubs is community-authored and mislabeled.
- Don't let GITHUB_TOKEN / .env harvesting in scraping skills go unreviewed (map-your-market & where-your-customer-lives ship .env.example and read GITHUB_TOKEN).
