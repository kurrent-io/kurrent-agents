/**
 * AG-UI BaseEvent stream → renderable message list.
 *
 * The middleware/replay agent emit message-grained events; we accumulate
 * them into a flat list of bubbles for the chat panel. Tool calls become
 * their own bubble; their results follow as another tool bubble.
 */

import { EventType, type BaseEvent } from './agent';

export interface TextBubble {
  kind: 'text';
  id: string;
  role: 'user' | 'assistant';
  content: string;
  pending?: boolean;
}

export interface ToolBubble {
  kind: 'tool';
  id: string;
  toolCallId: string;
  toolName: string;
  argsJson?: string;
  resultText?: string;
}

export interface ErrorBubble {
  kind: 'error';
  id: string;
  message: string;
}

export type Bubble = TextBubble | ToolBubble | ErrorBubble;

/** Accumulator state — keeps maps so streamed deltas can update existing bubbles. */
export interface MessageAccumulator {
  bubbles: Bubble[];
  byMessageId: Map<string, number>;
  byToolCallId: Map<string, number>;
}

export function emptyAccumulator(): MessageAccumulator {
  return { bubbles: [], byMessageId: new Map(), byToolCallId: new Map() };
}

/**
 * Apply one AG-UI event to the accumulator. Returns a new accumulator
 * (immutable update so React notices changes).
 */
export function applyEvent(
  prev: MessageAccumulator,
  event: BaseEvent,
): MessageAccumulator {
  const bubbles = [...prev.bubbles];
  const byMessageId = new Map(prev.byMessageId);
  const byToolCallId = new Map(prev.byToolCallId);

  const e = event as BaseEvent & Record<string, unknown>;

  switch (event.type) {
    case EventType.TEXT_MESSAGE_START: {
      const id = e.messageId as string;
      const role = (e.role as 'user' | 'assistant') ?? 'assistant';
      const idx = byMessageId.get(id);
      if (idx === undefined) {
        bubbles.push({ kind: 'text', id, role, content: '', pending: true });
        byMessageId.set(id, bubbles.length - 1);
      }
      break;
    }

    case EventType.TEXT_MESSAGE_CONTENT: {
      const id = e.messageId as string;
      const delta = (e.delta as string) ?? '';
      const idx = byMessageId.get(id);
      if (idx !== undefined && bubbles[idx]?.kind === 'text') {
        const b = bubbles[idx] as TextBubble;
        bubbles[idx] = { ...b, content: b.content + delta, pending: true };
      }
      break;
    }

    case EventType.TEXT_MESSAGE_END: {
      const id = e.messageId as string;
      const idx = byMessageId.get(id);
      if (idx !== undefined && bubbles[idx]?.kind === 'text') {
        bubbles[idx] = { ...(bubbles[idx] as TextBubble), pending: false };
      }
      break;
    }

    case EventType.TOOL_CALL_START: {
      const id = e.toolCallId as string;
      const tool = bubbles.length;
      bubbles.push({
        kind: 'tool',
        id: `tool-${id}`,
        toolCallId: id,
        toolName: (e.toolCallName as string) ?? 'unknown',
      });
      byToolCallId.set(id, tool);
      break;
    }

    case EventType.TOOL_CALL_ARGS: {
      const id = e.toolCallId as string;
      const idx = byToolCallId.get(id);
      const delta = (e.delta as string) ?? '';
      if (idx !== undefined && bubbles[idx]?.kind === 'tool') {
        const b = bubbles[idx] as ToolBubble;
        bubbles[idx] = { ...b, argsJson: (b.argsJson ?? '') + delta };
      }
      break;
    }

    case EventType.TOOL_CALL_RESULT: {
      const id = e.toolCallId as string;
      const content = (e.content as string) ?? '';
      const idx = byToolCallId.get(id);
      if (idx !== undefined && bubbles[idx]?.kind === 'tool') {
        const b = bubbles[idx] as ToolBubble;
        bubbles[idx] = { ...b, resultText: content };
      }
      break;
    }

    case EventType.RUN_ERROR: {
      bubbles.push({
        kind: 'error',
        id: `err-${Date.now()}`,
        message: (e.message as string) ?? 'Run error',
      });
      break;
    }

    default:
      // Ignore lifecycle (RUN_STARTED, RUN_FINISHED), TOOL_CALL_END,
      // CUSTOM, REASONING_*, etc. for v1. Phase 2 surfaces reasoning.
      break;
  }

  return { bubbles, byMessageId, byToolCallId };
}
