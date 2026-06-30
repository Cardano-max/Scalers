/**
 * studio-history.ts — pull the persisted, LABELED studio transcript.
 *
 * The role brainstorm (funnel_architect → copywriter → critic → jury) runs inside
 * the backend `brainstorm_with_roles` tool and is persisted to `studio_chat_turns`
 * — those role contributions are NOT carried on the AG-UI event stream, so we read
 * them back via the existing GraphQL `studioChatHistory` query. The persisted
 * transcript (operator + host + the four role cells, ordered by seq) is the source
 * of truth for the thread; the AG-UI stream only adds the live, in-flight host turn
 * on top while a run is streaming.
 *
 * Each persisted role becomes a `ChatTurn` with an accurate label — never a generic
 * "agent" — so the brainstorm renders as the real, attributed multi-agent exchange.
 */
import type { ChatTurn, StudioRole } from '@/lib/data/studio-adapter';

interface BackendChatTurn {
  id: string;
  sessionId: string;
  seq: number;
  role: string;
  text: string;
  model: string | null;
  createdAt: string;
}

/** Map a backend role string to a console StudioRole + human label. */
function mapRole(role: string): { role: StudioRole; label: string } {
  switch (role) {
    case 'operator':
      return { role: 'OPERATOR', label: 'You' };
    case 'host':
      return { role: 'SYSTEM', label: 'Studio Host' };
    case 'funnel_architect':
      return { role: 'STRATEGIST', label: 'Funnel Architect' };
    case 'copywriter':
      return { role: 'COPYWRITER', label: 'Copywriter' };
    case 'critic':
      return { role: 'CRITIC', label: 'Critic' };
    case 'jury':
      return { role: 'JURY', label: 'Jury' };
    default:
      return { role: 'SYSTEM', label: role || 'Studio' };
  }
}

function toChatTurn(t: BackendChatTurn): ChatTurn {
  const { role, label } = mapRole(t.role);
  return { id: t.id, role, label, text: t.text, at: t.createdAt };
}

const STUDIO_CHAT_HISTORY = `query StudioChatHistory($sessionId: String!) {
  studioChatHistory(sessionId: $sessionId) { id sessionId seq role text model createdAt }
}`;

/**
 * Fetch the persisted transcript for a session, mapped to labeled ChatTurns.
 * Throws on transport failure so the caller can keep the honest preview state.
 */
export async function fetchStudioHistory(
  graphqlUrl: string,
  sessionId: string,
  signal?: AbortSignal,
): Promise<ChatTurn[]> {
  const res = await fetch(graphqlUrl, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ query: STUDIO_CHAT_HISTORY, variables: { sessionId } }),
    signal,
  });
  if (!res.ok) throw new Error(`studio history HTTP ${res.status}`);
  const json = (await res.json()) as {
    data?: { studioChatHistory: BackendChatTurn[] };
    errors?: Array<{ message?: string }>;
  };
  if (json.errors?.length) throw new Error(json.errors[0]?.message ?? 'studio history error');
  return (json.data?.studioChatHistory ?? []).map(toChatTurn);
}
