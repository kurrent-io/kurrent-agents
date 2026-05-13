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
  /**
   * True for an optimistic user bubble that has been claimed by a
   * server-echoed `TEXT_MESSAGE_*(role=user)`. Suppresses subsequent
   * CONTENT/END deltas for that messageId so the typed text doesn't
   * get re-appended to itself.
   */
  claimed?: boolean;
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
  /**
   * FIFO of local-only bubble ids waiting to be reconciled with the
   * server's echoed user `TEXT_MESSAGE_START`. The next user-role
   * START claims the oldest entry and the local bubble adopts the
   * canonical messageId. Without this, every typed user message
   * renders twice (optimistic + server echo).
   */
  pendingUserLocalIds: string[];
}

export function emptyAccumulator(): MessageAccumulator {
  return {
    bubbles: [],
    byMessageId: new Map(),
    byToolCallId: new Map(),
    pendingUserLocalIds: [],
  };
}

/** Push a local-only optimistic user bubble onto the queue. */
export function enqueueOptimisticUser(prev: MessageAccumulator, bubble: TextBubble): MessageAccumulator {
  return {
    ...prev,
    bubbles: [...prev.bubbles, bubble],
    pendingUserLocalIds: [...prev.pendingUserLocalIds, bubble.id],
  };
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
  let pendingUserLocalIds = prev.pendingUserLocalIds;

  const e = event as BaseEvent & Record<string, unknown>;

  switch (event.type) {
    case EventType.TEXT_MESSAGE_START: {
      const id = e.messageId as string;
      const role = (e.role as 'user' | 'assistant') ?? 'assistant';

      // Reconcile a server-echoed user message with the optimistic
      // bubble we already rendered when the user clicked Send.
      if (role === 'user' && pendingUserLocalIds.length > 0) {
        const [localId, ...rest] = pendingUserLocalIds;
        const localIdx = bubbles.findIndex((b) => b.id === localId);
        if (localIdx !== -1 && bubbles[localIdx]!.kind === 'text') {
          const claimed = bubbles[localIdx] as TextBubble;
          bubbles[localIdx] = { ...claimed, id, claimed: true, pending: false };
          byMessageId.set(id, localIdx);
          pendingUserLocalIds = rest;
          break;
        }
        // Local bubble vanished (e.g. session reset); fall through to
        // create a new one as the server expects.
        pendingUserLocalIds = rest;
      }

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
        // A claimed bubble already has the full content from the
        // optimistic render; ignore the server's echoed deltas to
        // avoid doubling.
        if (b.claimed) break;
        bubbles[idx] = { ...b, content: b.content + delta, pending: true };
      }
      break;
    }

    case EventType.TEXT_MESSAGE_END: {
      const id = e.messageId as string;
      const idx = byMessageId.get(id);
      if (idx !== undefined && bubbles[idx]?.kind === 'text') {
        const b = bubbles[idx] as TextBubble;
        if (b.claimed) break;
        bubbles[idx] = { ...b, pending: false };
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

  return { bubbles, byMessageId, byToolCallId, pendingUserLocalIds };
}
