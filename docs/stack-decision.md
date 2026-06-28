# Stack Decision Record, Scalers Growth Engine (June 2026)

This records the verified tech stack for the engine, checked against current June 2026 practice, and corrects stale assumptions in the two design docs. Sources are at the end. The architecture shape from the design docs holds. Research sharpened the tool picks and corrected four external facts.

## Verdict in one line

LangGraph spine with Pydantic AI typed cells, Claude models plus one local model for cheap and cross-check work, self-hosted Meta and Gmail MCP connectors, Firecrawl-led research, Postgres with pgvector plus Redis plus MinIO, a CopilotKit and AG-UI portal, and Langfuse for evals and traces. All local Docker behind a Cloudflare tunnel. No AWS.

## Operator decision and guiding principle (locked, June 28)

LangGraph is the spine. We are not hand-building or maintaining a LangGraph-equivalent. LangGraph gives capabilities a custom graph cannot, plus community, framework, and real support, so we build on it. FastAPI stays only as the thin portal and webhook ingress, not as the engine.

Guiding trade-off for every call from here. Optimize first for the brain's intelligence and capability, and for the system to actually work and do wonders, reasoned from first principles. Do not sacrifice scalability or capability for determinism purism. Keep determinism only where it protects real actions, which means fixed control flow, typed cell outputs, idempotent side effects, and computed confidence routing. Let the framework provide durable state, human-in-the-loop interrupts, and recovery rather than hand-rolling them.

Dispositions on the alternatives super researched. Durability runs on the LangGraph Postgres checkpointer for durable state and crash recovery, with DBOS Transact and Temporal deferred unless later scale demands a second runtime, since two durability layers are redundant. Typed cells consolidate to Pydantic AI for native ecosystem fit, dropping BAML unless the team has a specific reason, and the validator bank stays either way. Exactly-once protection stays, with idempotency keys plus an outbox on every real send and post, because that is action safety rather than determinism for its own sake.

## Layer decisions

### Orchestration, the spine
Use LangGraph. It is still the top framework in 2026 for stateful, branching, retrying, human-in-the-loop workflows, with durable checkpointing so a crashed run resumes from the last step. Use the Postgres checkpointer. The model never picks the next step; the graph is fixed in code.

Defer Temporal. Add it only if volume or uptime later demands workflow-level durability across many runs. For one client, LangGraph checkpoints plus Postgres are enough. Temporal now integrates cleanly with LangGraph if we ever need it.

### Bounded agent cells, determinism
Use Pydantic AI for every LLM node. It sends a JSON schema with the prompt, validates the response on arrival, and auto-retries when a field is missing or mistyped. That is the typed-parser layer the design asks for, off the shelf. Temperature 0 on every decision and classify node. Scoring and routing are pure Python over concrete signals.

### Models and routing
Generation and judgment run on Claude Opus 4.8 for the hardest writing and judging, with Claude Sonnet 4.6 as the balanced default. Cheap classification, triage, and the brainstorm pre-pass run on Claude Haiku 4.5, or a local model through Ollama for near-zero cost. Embeddings run on a local model into pgvector, free and private.

Correction: the design's "free-tier model" is not a real Anthropic tier. Map it to Haiku or a local model.

### Quality gate, jury plus validators
Deterministic validators run first (banned phrase, claims, length, voice similarity). They carry most of the gating and never call a model. For the borderline band only, a small jury of judges scores the item, calibrated against a human-labeled gold set, with confidence pooled, and 5 to 10 percent of auto actions cross-checked by a human. Confidence is computed, never self-reported. Routing is a pure function of the score, the autonomy mode, and the deterministic gates.

Correction: best practice says do not use the same model family as generator and judge. The client only provides an Anthropic key, so add one local open model through Ollama as the cross-family juror. Claude jurors at temperature 0 with varied prompts back it up.

### Skills and plugins
Use the Anthropic Agent Skills format for the expert modules: brand voice per artist, angle, hook, reply, email. A skill is a folder of instructions, examples, and small scripts loaded on demand. This is now an adopted standard across Claude Code, Codex CLI, Cursor, Gemini CLI, and Copilot, so it is a safe base. Plugins are per-vertical bundles of skills and hooks, enabled per artist, which matches the conditional-plugins idea. The skills are built and tested through the operator's own skill-creator pipeline. The engine loads them, it does not author them.

### Connectors, the hands
Self-host a Meta MCP, forked from an official-API Instagram and Facebook server, for publish, comments, history, and insights. Official Graph API only, no private endpoints, no scraping against terms. Self-host a Gmail MCP for send, watch, and bounce. Add a thin MCP gateway for auth, scoping, and tracing only if the connector count grows.

### Research brain
Firecrawl is the primary external research tool. It does search plus scrape in one call, returns clean markdown, and ranked best overall for RAG pipelines in 2026 benchmarks. Free 500 pages, then cheap. Exa is optional for semantic discovery behind the same adapter. For competitor ads, Foreplay's Competitor Advertising API (foreplay.co/api) is the primary source. Every Foreplay customer now gets 10,000 free API credits a month, one ad per credit, across more than 100 million ads and 300k-plus brands with creative metadata. The operator already has Foreplay access. The free Meta Ad Library is the fallback. All of these sit behind the same pluggable adapter.

Correction (June 28). This reverses the earlier note that said Foreplay has no API. Foreplay does have an API in 2026, so it is in as the primary competitor-ad source. Reddit is the one still out, its commercial use needs an enterprise contract and the free PRAW tier forbids commercial use, so keep Reddit out of the MVP brain or use it only non-commercial and low volume. The research layer is pluggable so one failing source never blocks a run.

### Frontend
Next.js with CopilotKit on the AG-UI protocol. It is the adopted standard for connecting agents to a frontend in 2026, used by Google, AWS, Microsoft, LangChain, Mastra, and Pydantic AI, with streaming, generative UI, and inline approvals over server-sent events. Build to the Scalers Operator Console design, then run the result through the unslop-ui skill so it does not read as AI-generated.

### Data and runtime
Postgres 16 with pgvector holds state, the append-only status audit, structured data, LangGraph checkpoints, and vectors. Redis streams run the queue and the per-artist scheduler. MinIO holds assets and provided creatives. Everything runs in a local Docker Compose stack, reached through a Cloudflare tunnel with no open port. No AWS.

### Evals and observability
Langfuse, self-hosted. It is the best free, open-source, self-hostable option in 2026, now ClickHouse-backed, good for traces, prompt versions, and evals on a local stack. Add promptfoo or DeepEval for CI-style eval gating per release. Pin model versions and re-run evals on every bump.

## Platform limits to enforce in code (verified June 2026)

Instagram publishing is not a flat 100 per day. Reports range from 25 to 100 per rolling 24 hours by account trust and age. Query the account's content_publishing_limit endpoint at run time and default conservative at 25. Reels and stories count in the same bucket.

Instagram DMs are tightly limited. Auto replies are only allowed inside the 24-hour window the user opens by messaging first. The 7-day HUMAN_AGENT extension is for real humans only; automating it is a policy violation and a fast way to lose API access. Hard caps are about 200 automated DMs per hour per account, plus a 1-DM-per-user-per-24-hours cap on comment and story triggers. So the engine auto-replies to comments, which are public and safer, and only auto-DMs inside the open window under the caps. Everything else routes to a human.

Meta app review is still the 2 to 4 week long pole for publish and comment scopes. Submit on day one, run the client account in dev or standard mode meanwhile, and keep it off the critical path.

Cold email from Gmail never goes out on the client's main domain. Use a separate sending domain or workspace, with SPF, DKIM, DMARC, a warmup ramp, low per-inbox volume, one-click unsubscribe, and the suppression list.

## What stays from the design

The whole shape holds. A deterministic LangGraph harness, typed cells, computed confidence, idempotency keys, self-hosted MCPs, three engines on a shared core, and local Docker. The corrections above change four external facts and a few tool picks, not the architecture.

## Sources

- [LangChain, best AI agent frameworks 2026](https://www.langchain.com/resources/ai-agent-frameworks)
- [DEV, 2026 framework decision guide, LangGraph vs CrewAI vs Pydantic AI](https://dev.to/linou518/the-2026-ai-agent-framework-decision-guide-langgraph-vs-crewai-vs-pydantic-ai-b2h)
- [Pydantic AI, GitHub](https://github.com/pydantic/pydantic-ai)
- [Durable execution for LLM agents 2026, Temporal plus LangGraph](https://appscale.blog/en/blog/durable-execution-llm-agents-temporal-langgraph-checkpointing-2026)
- [CopilotKit, AG-UI protocol](https://www.copilotkit.ai/ag-ui)
- [LLM-as-judge best practices 2026](https://futureagi.com/blog/llm-as-judge-best-practices-2026)
- [DeepEval, LLM-as-a-judge 2026](https://deepeval.com/blog/llm-as-a-judge)
- [Langfuse alternatives and agent observability 2026](https://laminar.sh/article/langfuse-alternatives-2026)
- [Anthropic, equipping agents with Agent Skills](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills)
- [Firecrawl vs Exa for AI agents 2026](https://www.firecrawl.dev/alternatives/firecrawl-vs-exa)
- [AIMultiple, agentic search benchmark](https://aimultiple.com/agentic-search)
- [Instagram content publishing limit, Meta developer docs](https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/content_publishing_limit/)
- [Instagram API rate limits 2026](https://www.getphyllo.com/post/instagram-api-rate-limits-explained----and-how-to-scale-beyond-them-2026)
- [Instagram messaging API 24-hour window policy 2026](https://www.keyapi.ai/blog/instagram-messaging-api-policy/)
- [Meta Messenger and IG messaging policy](https://developers.facebook.com/documentation/business-messaging/messenger-platform/policy)
- [Reddit API pricing 2026](https://octolens.com/blog/reddit-api-pricing)
