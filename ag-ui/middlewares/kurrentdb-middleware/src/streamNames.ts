/**
 * Canonical stream-name builders. Mirrors schema/python/.../streams.py
 * and Kurrent.Agent.Schema.StreamNames (.NET).
 *
 * See schema/SCHEMA_v2.md §2.
 */

export const AGENT_SESSION_PREFIX = 'AgentSession-';

export function agentSessionStream(sessionId: string): string {
  return `${AGENT_SESSION_PREFIX}${sessionId}`;
}
