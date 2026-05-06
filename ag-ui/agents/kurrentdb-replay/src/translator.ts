/**
 * Pure canonical-event → AG-UI events translator (read side, DEV-1559).
 *
 * Mirrors the inverse of the DEV-1558 write path in
 * `ag-ui/middlewares/kurrentdb-middleware/src/translator.ts`. Where the
 * write side collapses incremental AG-UI events to one canonical event
 * per message at RUN_FINISHED, the read side fabricates the full event
 * triples back from each canonical event (one start, one content, one
 * end — replay is message-grained, not token-grained).
 *
 * Mapping (also in ag-ui/GAPS.md, schema/SCHEMA_v2.md §3):
 *   SessionStarted              → RUN_STARTED
 *   SessionEnded                → RUN_FINISHED
 *   SessionContinuedAs          → CUSTOM (session.continued_as)
 *   UserMessageReceived         → TEXT_MESSAGE_START(role=user) → _CONTENT → _END
 *   AssistantTextGenerated      → TEXT_MESSAGE_START(role=assistant) → _CONTENT → _END
 *   AssistantThinkingGenerated  → REASONING_MESSAGE_START → _CONTENT → _END
 *                                 (skipped if encrypted=true and no plaintext)
 *   AssistantToolCallsGenerated → optional carrier TEXT_MESSAGE_* if content set;
 *                                 per call: TOOL_CALL_START → _ARGS(JSON delta) → _END
 *   ToolResultReceived          → TOOL_CALL_RESULT
 *   InterruptIssued             → CUSTOM (interrupt.issued)
 *   InterruptResolved           → CUSTOM (interrupt.resolved)
 *   SubagentStarted/Completed   → CUSTOM
 *   Other / framework-specific  → CUSTOM (canonical.<type>) when passthrough on
 *
 * Pure: no I/O, mutates only the small `ReplayContext`. Caller serialises.
 */

import { EventType, type BaseEvent } from '@ag-ui/client';

export interface ReplayContext {
  threadId: string;
  runId: string;
  /** Track the most recent assistant messageId so tool calls carry parentMessageId. */
  lastAssistantMessageId?: string;
  /** Whether RUN_FINISHED has been emitted (idempotency guard). */
  finished: boolean;
  /**
   * Whether unknown canonical event types emit CUSTOM events.
   * Default true — allows downstream UIs to surface framework-specific
   * extensions without dropping data.
   */
  customPassthrough: boolean;
}

export function makeContext(threadId: string, runId: string): ReplayContext {
  return {
    threadId,
    runId,
    finished: false,
    customPassthrough: true,
  };
}

/**
 * Translate one canonical event into zero or more AG-UI events.
 * Mutates `ctx` (e.g. updates `lastAssistantMessageId`, sets `finished`).
 */
export function translateCanonical(
  eventType: string,
  payload: Record<string, unknown>,
  ctx: ReplayContext,
): BaseEvent[] {
  const handler = HANDLERS[eventType];
  if (handler) return handler(payload, ctx);
  if (ctx.customPassthrough) return [custom(`canonical.${eventType}`, payload)];
  return [];
}

// --------------------------------------------------------------- handlers

function onSessionStarted(_p: Record<string, unknown>, ctx: ReplayContext): BaseEvent[] {
  return [
    {
      type: EventType.RUN_STARTED,
      threadId: ctx.threadId,
      runId: ctx.runId,
    } as BaseEvent,
  ];
}

function onSessionEnded(_p: Record<string, unknown>, ctx: ReplayContext): BaseEvent[] {
  if (ctx.finished) return [];
  ctx.finished = true;
  return [
    {
      type: EventType.RUN_FINISHED,
      threadId: ctx.threadId,
      runId: ctx.runId,
    } as BaseEvent,
  ];
}

function onUserMessage(p: Record<string, unknown>, _ctx: ReplayContext): BaseEvent[] {
  const messageId = (p.message_id as string) ?? synthId('usr', p);
  const content = (p.content as string) ?? '';
  return textMessage(messageId, 'user', content);
}

function onAssistantText(p: Record<string, unknown>, ctx: ReplayContext): BaseEvent[] {
  const messageId = (p.message_id as string) ?? synthId('asst', p);
  ctx.lastAssistantMessageId = messageId;
  const content = (p.content as string) ?? '';
  return textMessage(messageId, 'assistant', content);
}

function onAssistantThinking(p: Record<string, unknown>, _ctx: ReplayContext): BaseEvent[] {
  const encrypted = p.encrypted === true;
  const content = (p.content as string) ?? '';
  if (encrypted && !content) return [];
  const messageId = (p.message_id as string) ?? synthId('rsn', p);
  const out: BaseEvent[] = [
    { type: EventType.REASONING_MESSAGE_START, messageId } as BaseEvent,
  ];
  if (content) {
    out.push({
      type: EventType.REASONING_MESSAGE_CONTENT,
      messageId,
      delta: content,
    } as BaseEvent);
  }
  out.push({ type: EventType.REASONING_MESSAGE_END, messageId } as BaseEvent);
  return out;
}

function onAssistantToolCalls(p: Record<string, unknown>, ctx: ReplayContext): BaseEvent[] {
  const messageId = (p.message_id as string) ?? synthId('asst', p);
  const out: BaseEvent[] = [];
  const carrier = p.content as string | undefined;
  if (carrier) {
    out.push(...textMessage(messageId, 'assistant', carrier));
  }
  ctx.lastAssistantMessageId = messageId;
  const calls =
    (p.tool_calls as Array<{
      call_id?: string;
      tool_name?: string;
      arguments?: Record<string, unknown>;
    }>) ?? [];
  for (const call of calls) {
    const callId = call.call_id ?? synthId('call', call as Record<string, unknown>);
    const toolName = call.tool_name ?? '';
    const args = call.arguments ?? {};
    out.push({
      type: EventType.TOOL_CALL_START,
      toolCallId: callId,
      toolCallName: toolName,
      parentMessageId: messageId,
    } as BaseEvent);
    out.push({
      type: EventType.TOOL_CALL_ARGS,
      toolCallId: callId,
      delta: JSON.stringify(args),
    } as BaseEvent);
    out.push({ type: EventType.TOOL_CALL_END, toolCallId: callId } as BaseEvent);
  }
  return out;
}

function onToolResult(p: Record<string, unknown>, _ctx: ReplayContext): BaseEvent[] {
  const callId = (p.call_id as string) ?? '';
  const result = p.result;
  // Canonical ToolResultReceived.result is a string per SCHEMA_v2 §3
  // (frameworks JSON-encode structured returns before writing).
  const content =
    typeof result === 'string' ? result : JSON.stringify(result ?? '');
  const messageId = (p.message_id as string) ?? synthId('tres', p);
  return [
    {
      type: EventType.TOOL_CALL_RESULT,
      messageId,
      toolCallId: callId,
      content,
      role: 'tool',
    } as BaseEvent,
  ];
}

// ---------------------------------------------------------------- helpers

function textMessage(messageId: string, role: 'user' | 'assistant', content: string): BaseEvent[] {
  const out: BaseEvent[] = [
    { type: EventType.TEXT_MESSAGE_START, messageId, role } as BaseEvent,
  ];
  if (content) {
    out.push({
      type: EventType.TEXT_MESSAGE_CONTENT,
      messageId,
      delta: content,
    } as BaseEvent);
  }
  out.push({ type: EventType.TEXT_MESSAGE_END, messageId } as BaseEvent);
  return out;
}

function custom(name: string, value: unknown): BaseEvent {
  return { type: EventType.CUSTOM, name, value } as BaseEvent;
}

function synthId(prefix: string, payload: Record<string, unknown>): string {
  const ts = (payload.timestamp as string) ?? (payload.created_at as string) ?? '';
  const idx = payload.message_index;
  if (ts) return `${prefix}-${ts}-${idx ?? ''}`;
  return `${prefix}-${hashStable(payload)}`;
}

function hashStable(value: unknown): string {
  // Cheap stable-ish hash for fallback ids — never security-critical.
  const s = JSON.stringify(value, Object.keys(value as object).sort());
  let h = 0;
  for (let i = 0; i < s.length; i++) {
    h = ((h << 5) - h + s.charCodeAt(i)) | 0;
  }
  return Math.abs(h).toString(36);
}

const HANDLERS: Record<string, (p: Record<string, unknown>, ctx: ReplayContext) => BaseEvent[]> = {
  SessionStarted: onSessionStarted,
  SessionEnded: onSessionEnded,
  SessionContinuedAs: (p) => [custom('session.continued_as', p)],
  UserMessageReceived: onUserMessage,
  AssistantTextGenerated: onAssistantText,
  AssistantThinkingGenerated: onAssistantThinking,
  AssistantToolCallsGenerated: onAssistantToolCalls,
  ToolResultReceived: onToolResult,
  InterruptIssued: (p) => [custom('interrupt.issued', p)],
  InterruptResolved: (p) => [custom('interrupt.resolved', p)],
  SubagentStarted: (p) => [custom('subagent.started', p)],
  SubagentCompleted: (p) => [custom('subagent.completed', p)],
};
