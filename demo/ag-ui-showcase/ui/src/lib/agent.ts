/**
 * AG-UI client wrapper for the showcase UI.
 *
 * Speaks AG-UI HTTP/SSE to /agent/{framework} (proxied to the TS server
 * on port 7000 via Vite). Same wire protocol the server expects.
 */

import { EventType, HttpAgent, type BaseEvent } from '@ag-ui/client';

export type Framework = 'maf' | 'adk' | 'strands' | 'langgraph';

export const FRAMEWORK_LABELS: Record<Framework, string> = {
  maf: 'Microsoft Agent Framework',
  adk: 'Google ADK',
  strands: 'Strands',
  langgraph: 'LangGraph (AG-UI native)',
};

export const FRAMEWORK_ENABLED: Record<Framework, boolean> = {
  maf: true,
  adk: false, // Phase 2
  strands: false, // Phase 2
  langgraph: false, // Phase 2
};

export function makeAgent(framework: Framework, threadId: string): HttpAgent {
  return new HttpAgent({
    url: `/agent/${framework}`,
    threadId,
  });
}

export { EventType };
export type { BaseEvent };
