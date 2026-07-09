# Updated console (v2) — richer observability + clickable nav

Source of truth design: the **125KB** `frontend-development-help/project/Scalers Operator Console.dc.html`
(extracted at C:\Users\Links\AppData\Local\Temp\claude\C--Users-Links-Desktop-CustomerAcq-super\2ecbc0f7-c1bb-4e56-8fc9-afc7bcd58754\scratchpad\handoff-v2\...).
New vs the 106KB version (already built): Runs screen with per-step span trees, Activity EXECUTION TRACE + per-judge JURY card + links to the real post/comment/email, Live feed + Command screens, clickable nav everywhere.

## New GraphQL types + fields (camelCase; FE queries.ts must request exactly these)
```
type Span { kind: String!  title: String!  ms: Int  detail: String! }   # kind: tool|llm|jury|gate|decision
type RunEvent { worker: String!  text: String!  severity: String!  ms: String!  spans: [Span!]! }
type Judge { name: String!  score: Float!  vote: String!  reasoning: String! }
type ExecutionTrace { id: ID!  latency: String!  model: String!  tokens: String! }
type ActivityLink { label: String!  target: String!  targetType: String! }   # POST|COMMENT|DM|EMAIL

extend Run        { events: [RunEvent!]! }          # keep trajectory for back-compat
extend ActivityItem { runId: ID  trace: ExecutionTrace  judges: [Judge!]!  spans: [Span!]!  links: [ActivityLink!]! }   # keep thinking for back-compat
```
FE `queries.ts`:
- RUN_FIELDS += `events { worker text severity ms spans { kind title ms detail } }`
- ACTIVITY_FIELDS += `runId trace { id latency model tokens } judges { name score vote reasoning } spans { kind title ms detail } links { label target targetType }`

FE `models.ts`: add the matching TS interfaces (Span, RunEvent, Judge, ExecutionTrace, ActivityLink); add the fields to Run + ActivityItem.

## Backend data mapping (real data — no fabrication; mark derived/unknown honestly)
- **Run.events** ← `runs.steps` JSONB (each span: at/seq/kind/node/text/input/output/state/status/span_id/children/duration_ms/parent_span_id). Group top-level spans (parent_span_id null) into RunEvents: worker=node label, text=span text, severity from status (ok→info, failed→error), ms="{duration_ms/1000}s". Each event's `spans` = that span's `children` mapped to Span{kind (map cell→llm/tool by node name; node→tool), title=node/cell name, ms=duration_ms, detail=output or input (the per-step "internal thought")}.
- **ActivityItem.spans** ← if the action has no linked run, synthesize the REAL decision trace from autonomy_decisions+autonomy_jury: one 'llm' span (draft), one 'jury' span per judge (detail=its voice/safety/appr scores), one 'gate' span per gate, one 'decision' span (route+confidence). All real values.
- **ActivityItem.judges** ← autonomy_jury rows: name=judge, score=mean(voice,safety,appr), vote = hard_fail? 'fail':'pass', reasoning = "voice X · safety Y · appr Z" (derived from the real per-dim scores — honest, not invented).
- **ActivityItem.trace** ← id=decision_id, latency=sum span durations or "—", model = the cell model if known else "—", tokens "—" if not captured (honest).
- **ActivityItem.links** ← from the action's deep_link once sent: {label "View email"/"View post"/"View reply", target=deep_link, targetType from channel/type}. Empty if not sent.
- **ActivityItem.runId** ← action.run_id if present else null.

## Screens to build (web/components)
- RunsScreen.tsx (list + drawer: RUN HISTORY events, each clickable→expand nested span tree; "Open in Activity →" + "Show in live feed →" nav)
- FeedScreen.tsx (full live feed; filter chips; pause)
- CommandScreen.tsx (chat to the harness via sendCommand)
- ActivityScreen.tsx ENRICH: EXECUTION TRACE card (trace meta + spans tree), JURY card (per-judge), LINKS row (nav to real post/comment/email). Keep the existing engagement/thread.
- screens.tsx: register runs/feed/command components (replace the pending() stubs).
- Honor design tokens (teal=tool/llm/auto, amber=jury/human, green=gate/decision; IBM Plex Mono for ids/durations).
