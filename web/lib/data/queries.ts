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
  jury { confidence threshold agreement dimensions { label score } }
  gates { label ok }
  recommendation idempotencyKey status
`;

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
  runId trace { id latency model tokens }
  judges { name score vote reasoning }
  spans { kind title ms detail }
  links { label target targetType }
`;

const RUN_FIELDS = `
  id tenantId type trigger status startedAt duration autoCount reviewCount
  retries idempotencyKey channels trajectory { at text state } note
  events { worker text severity ms spans { kind title ms detail } }
`;

const FEED_FIELDS = `id tenantId worker text at chip severity actionId runId decisionId`;
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
    reviewQueue(tenantId: $tenantId, filter: $filter) { ${ACTION_FIELDS} }
  }
`;

export const ACTION_QUERY = `
  query Action($id: ID!) { action(id: $id) { ${ACTION_FIELDS} } }
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
export const APPROVE_ACTION = `
  mutation ApproveAction($id: ID!, $idempotencyKey: String!) {
    approveAction(id: $id, idempotencyKey: $idempotencyKey) { ${ACTION_FIELDS} }
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
