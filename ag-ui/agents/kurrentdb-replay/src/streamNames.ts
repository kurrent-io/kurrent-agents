/**
 * Canonical stream-name builders. Mirrors
 * `ag-ui/middlewares/kurrentdb-middleware/src/streamNames.ts` and
 * `schema/python/.../streams.py` — the canonical stream taxonomy is
 * shared across packages and languages.
 *
 * See schema/SCHEMA_v2.md §2.
 */

export const AGENT_SESSION_PREFIX = 'AgentSession-';

export function agentSessionStream(sessionId: string): string {
  return `${AGENT_SESSION_PREFIX}${sessionId}`;
}
