/**
 * GraphQL documents for the kkg.4 read API (queries + mutations). These mirror
 * scalers-backend-plan §1.2/§1.3 field-for-field so the live adapter binds to
 * the real gateway with no reshaping. Kept as plain strings (urql accepts
 * string documents) to avoid a codegen step before the SDL ships.
 */

const ACTION_FIELDS = `
  id tenantId type channel worker target createdAt subject context draft
  confidence threshold
  escalation { kind label }
  jury {
    confidence threshold agreement selfConsistency
    dimensions { label score verdict threshold jurorBreakdown { judge score vote } }
    judges { judge family voice safety appr overall }
  }
  gates { label ok }
  recommendation idempotencyKey status lastError
  judges { name score vote reasoning }
  runId campaignId agentRole agentStepId traceUrl
`;
// runId/campaignId/agentRole/agentStepId/traceUrl are the traceability-spine
// lineage now exposed on every Action (Review queue + Activity, which embeds this
// fragment). Honest-null when the backend has no source. They let the chips
// deep-link a draft → its run / producing-agent reasoning / run-level trace.
// lastError carries the REAL provider error on a FAILED send (null otherwise).
// It lives on BOTH Action and ActivityItem, so keeping it in the shared fragment
// resolves for the review queue / action detail AND the Activity query (which
// embeds ACTION_FIELDS) — unlike isSeeded, which is Action-only.
// isSeeded lives only on the Action type (review queue / action detail), NOT on
// ActivityItem — keep it out of the shared fragment so the Activity query (which
// embeds ACTION_FIELDS) doesn't request a field ActivityItem lacks.

// Activity (executed actions) = the Action core + the handoff reasoning/engagement
// extensions. Mirrors the `ActivityItem` model field-for-field.
const ACTIVITY_FIELDS = `
  ${ACTION_FIELDS}
  autonomy content
  outcome { label kind }
  thinking
  engagement { label value }
  thread { role name text }
  comments { name text autoReplied }
  trace { id latency model tokens }
  judges { name score vote reasoning }
  spans { kind title ms detail }
  links { label target targetType }
`;
// runId / campaignId / agentRole / agentStepId / traceUrl arrive via the embedded
// ACTION_FIELDS fragment above (don't re-list them here — that would double-select).

const RUN_FIELDS = `
  id tenantId type trigger status startedAt duration autoCount reviewCount
  retries idempotencyKey channels trajectory { at text state } note traceUrl campaignId
  events { worker text severity ms actionId runId decisionId spans { kind title ms detail } }
`;

const FEED_FIELDS = `id tenantId worker text at chip severity actionId runId decisionId campaignId`;
const KPIS_FIELDS = `autonomyPct reviewQueueCount outreachToday complaintsPct commentsAuto commentsReview postsPublished postsScheduled`;
const HEALTH_FIELDS = `emailComplaintRate emailBounceRate gmailWarmupUsed gmailWarmupCap igPublishUsed igPublishCap checkpointStatus`;

export const TENANT_QUERY = `
  query Tenant($id: ID!) {
    tenant(id: $id) {
      id name pack channels engineState
      autonomy { channel mode threshold held }
    }
  }
`;

export const OVERVIEW_QUERY = `
  query Overview($tenantId: ID!) {
    overview(tenantId: $tenantId) {
      kpis { ${KPIS_FIELDS} }
      attention { ${ACTION_FIELDS} }
      recentRuns { ${RUN_FIELDS} }
      systemHealth { ${HEALTH_FIELDS} }
      feedPreview { ${FEED_FIELDS} }
    }
  }
`;

export const REVIEW_QUEUE_QUERY = `
  query ReviewQueue($tenantId: ID!, $filter: ActionFilter) {
    reviewQueue(tenantId: $tenantId, filter: $filter) { ${ACTION_FIELDS} isSeeded }
  }
`;

export const ACTION_QUERY = `
  query Action($id: ID!) { action(id: $id) { ${ACTION_FIELDS} isSeeded } }
`;

export const ACTIVITY_QUERY = `
  query Activity($tenantId: ID!, $filter: ActionFilter) {
    activity(tenantId: $tenantId, filter: $filter) { ${ACTIVITY_FIELDS} }
  }
`;

export const ACTIVITY_ITEM_QUERY = `
  query ActivityItem($id: ID!) { activityItem(id: $id) { ${ACTIVITY_FIELDS} } }
`;

export const RUNS_QUERY = `
  query Runs($tenantId: ID!, $filter: RunFilter) {
    runs(tenantId: $tenantId, filter: $filter) { ${RUN_FIELDS} }
  }
`;

export const RUN_QUERY = `
  query Run($id: ID!) { run(id: $id) { ${RUN_FIELDS} } }
`;

export const CAMPAIGN_SPEC_QUERY = `
  query CampaignSpec($runId: ID!) {
    campaignSpec(runId: $runId) {
      runId campaignId tenantId archetypeId markdown contentJson createdAt updatedAt
    }
  }
`;

export const FEED_QUERY = `
  query Feed($tenantId: ID!, $filter: FeedFilter, $after: ID, $limit: Int) {
    feed(tenantId: $tenantId, filter: $filter, after: $after, limit: $limit) { ${FEED_FIELDS} }
  }
`;

export const SYSTEM_HEALTH_QUERY = `
  query SystemHealth($tenantId: ID!) {
    systemHealth(tenantId: $tenantId) { ${HEALTH_FIELDS} }
  }
`;

// --- mutations (wired by the action/command beads; surface ready here) ---
// approveAction also selects `mode` (the resolved send mode of THIS approve, 'live' |
// 'test_redirect'). It is Action-only and transient, so it is requested HERE on the
// mutation rather than in the shared ACTION_FIELDS fragment (which ActivityItem embeds
// and which has no `mode` field) — same reasoning as isSeeded.
export const APPROVE_ACTION = `
  mutation ApproveAction($id: ID!, $idempotencyKey: String!, $live: Boolean) {
    approveAction(id: $id, idempotencyKey: $idempotencyKey, live: $live) { ${ACTION_FIELDS} mode }
  }
`;
export const REJECT_ACTION = `
  mutation RejectAction($id: ID!, $reason: String) {
    rejectAction(id: $id, reason: $reason) { ${ACTION_FIELDS} }
  }
`;
export const EDIT_ACTION_DRAFT = `
  mutation EditActionDraft($id: ID!, $draft: String!) {
    editActionDraft(id: $id, draft: $draft) { ${ACTION_FIELDS} }
  }
`;
export const REGENERATE_ACTION = `
  mutation RegenerateAction($id: ID!) {
    regenerateAction(id: $id) { ${ACTION_FIELDS} }
  }
`;
export const SET_ENGINE_STATE = `
  mutation SetEngineState($tenantId: ID!, $paused: Boolean!) {
    setEngineState(tenantId: $tenantId, paused: $paused)
  }
`;
export const SET_AUTONOMY = `
  mutation SetAutonomy($tenantId: ID!, $channel: Channel!, $mode: AutonomyMode!, $threshold: Float!) {
    setAutonomy(tenantId: $tenantId, channel: $channel, mode: $mode, threshold: $threshold) {
      channel mode threshold held
    }
  }
`;
export const SEND_COMMAND = `
  mutation SendCommand($tenantId: ID!, $text: String!) {
    sendCommand(tenantId: $tenantId, text: $text) { id role text label at }
  }
`;
export const START_CAMPAIGN = `
  mutation StartCampaign($tenantId: ID!, $brief: CampaignBrief!) {
    startCampaign(tenantId: $tenantId, brief: $brief) { runId actionIds status }
  }
`;
