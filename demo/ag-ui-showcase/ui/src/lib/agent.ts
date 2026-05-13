/**
 * AG-UI client wrapper for the showcase UI.
 *
 * Speaks AG-UI HTTP/SSE to /agent/{framework} (proxied to the TS server
 * on port 7000 via Vite). Same wire protocol the server expects.
 */

import { EventType, HttpAgent, type BaseEvent } from '@ag-ui/client';

export type Framework = 'maf' | 'adk' | 'strands';

export const FRAMEWORK_LABELS: Record<Framework, string> = {
  maf: 'Microsoft Agent Framework',
  adk: 'Google ADK (pending bug fix)',
  strands: 'Strands',
};

export const FRAMEWORK_ENABLED: Record<Framework, boolean> = {
  maf: true,
  // kurrent_google_adk imports kurrent_agent_schema.events which moved
  // when schema 0.4.0 went proto-generated. Re-enable once the
  // integration migrates.
  adk: false,
  strands: true,
};

export function makeAgent(framework: Framework, threadId: string): HttpAgent {
  return new HttpAgent({
    url: `/agent/${framework}`,
    threadId,
  });
}

export { EventType };
export type { BaseEvent };
