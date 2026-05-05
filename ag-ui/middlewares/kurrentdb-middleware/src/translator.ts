/**
 * Pure AG-UI Message → canonical event translator.
 *
 * Translates a *committed* AG-UI message (one entry from
 * `EventWithState.messages`) into zero or more canonical events. No I/O,
 * no state — caller is responsible for tracking seen messageIds and
 * appending the produced events.
 *
 * Mapping (see ag-ui/GAPS.md and schema/SCHEMA_v2.md §3):
 *   UserMessage                                     → UserMessageReceived
 *   AssistantMessage with toolCalls.length === 0    → AssistantTextGenerated
 *   AssistantMessage with toolCalls.length >= 1     → AssistantToolCallsGenerated (carries optional content as carrier text)
 *   ReasoningMessage                                → AssistantThinkingGenerated
 *   ToolMessage                                     → ToolResultReceived
 *   SystemMessage / DeveloperMessage / ActivityMessage → skip in v1
 */

import type { Message } from '@ag-ui/client';

import type { CanonicalEvent } from './types.js';

export function translateMessage(message: Message, now: () => string = () => new Date().toISOString()): CanonicalEvent[] {
  const ts = now();
  const role = (message as { role?: string }).role;
  const id = (message as { id?: string }).id;
  if (!id) return []; // Defensive: AG-UI requires id, but skip rather than throw.

  switch (role) {
    case 'user': {
      const m = message as { id: string; role: 'user'; content?: string; name?: string };
      return [
        {
          type: 'UserMessageReceived',
          payload: {
            content: m.content ?? '',
            message_id: m.id,
            author_name: m.name,
            created_at: ts,
            timestamp: ts,
          },
        },
      ];
    }

    case 'assistant': {
      const m = message as {
        id: string;
        role: 'assistant';
        content?: string;
        toolCalls?: Array<{ id: string; type: 'function'; function: { name: string; arguments: string } }>;
        name?: string;
      };
      const calls = m.toolCalls ?? [];
      if (calls.length === 0) {
        return [
          {
            type: 'AssistantTextGenerated',
            payload: {
              content: m.content ?? '',
              message_id: m.id,
              author_name: m.name,
              created_at: ts,
              timestamp: ts,
            },
          },
        ];
      }
      return [
        {
          type: 'AssistantToolCallsGenerated',
          payload: {
            tool_calls: calls.map((c) => ({
              call_id: c.id,
              tool_name: c.function.name,
              arguments: parseArguments(c.function.arguments),
            })),
            content: m.content,
            message_id: m.id,
            author_name: m.name,
            created_at: ts,
            timestamp: ts,
          },
        },
      ];
    }

    case 'tool': {
      const m = message as {
        id: string;
        role: 'tool';
        content: string;
        toolCallId: string;
        name?: string;
      };
      return [
        {
          type: 'ToolResultReceived',
          payload: {
            call_id: m.toolCallId,
            tool_name: m.name ?? '',
            result: m.content,
            message_id: m.id,
            created_at: ts,
            timestamp: ts,
          },
        },
      ];
    }

    case 'reasoning': {
      const m = message as {
        id: string;
        role: 'reasoning';
        content?: string;
        encryptedValue?: string;
        name?: string;
      };
      const encrypted = m.encryptedValue !== undefined && (m.content === undefined || m.content === '');
      return [
        {
          type: 'AssistantThinkingGenerated',
          payload: {
            content: m.content,
            encrypted,
            message_id: m.id,
            author_name: m.name,
            created_at: ts,
            timestamp: ts,
          },
        },
      ];
    }

    // System / Developer / Activity messages: not part of canonical
    // conversation vocabulary. Skip in v1.
    default:
      return [];
  }
}

/**
 * AG-UI ToolCall.function.arguments is a string (frequently JSON, occasionally a
 * partial fragment for streaming or a plain literal). Parse if it looks like
 * JSON; otherwise pass through under "_raw" so the value isn't lost.
 */
function parseArguments(raw: string): Record<string, unknown> {
  if (!raw) return {};
  try {
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      return parsed as Record<string, unknown>;
    }
    return { _value: parsed };
  } catch {
    return { _raw: raw };
  }
}
