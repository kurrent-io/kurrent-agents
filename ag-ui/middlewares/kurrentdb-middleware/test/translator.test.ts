import { describe, expect, test } from 'vitest';

import { translateMessage } from '../src/translator.js';

const fixedNow = () => '2026-05-05T12:00:00.000Z';

describe('translateMessage', () => {
  test('UserMessage → UserMessageReceived', () => {
    const out = translateMessage(
      { id: 'u1', role: 'user', content: 'hi', name: 'alice' } as never,
      fixedNow,
    );
    expect(out).toEqual([
      {
        type: 'UserMessageReceived',
        payload: {
          content: 'hi',
          message_id: 'u1',
          author_name: 'alice',
          created_at: '2026-05-05T12:00:00.000Z',
          timestamp: '2026-05-05T12:00:00.000Z',
        },
      },
    ]);
  });

  test('AssistantMessage with no tool calls → AssistantTextGenerated', () => {
    const out = translateMessage(
      { id: 'a1', role: 'assistant', content: 'hello' } as never,
      fixedNow,
    );
    expect(out).toHaveLength(1);
    expect(out[0]!.type).toBe('AssistantTextGenerated');
    expect(out[0]!.payload).toMatchObject({
      content: 'hello',
      message_id: 'a1',
    });
  });

  test('AssistantMessage with tool calls → AssistantToolCallsGenerated (batched)', () => {
    const out = translateMessage(
      {
        id: 'a2',
        role: 'assistant',
        content: 'Let me check.',
        toolCalls: [
          {
            id: 'c1',
            type: 'function',
            function: { name: 'get_weather', arguments: '{"city":"Tokyo"}' },
          },
          {
            id: 'c2',
            type: 'function',
            function: { name: 'get_time', arguments: '{"tz":"Asia/Tokyo"}' },
          },
        ],
      } as never,
      fixedNow,
    );
    expect(out).toHaveLength(1);
    expect(out[0]!.type).toBe('AssistantToolCallsGenerated');
    const p = out[0]!.payload as {
      tool_calls: Array<{ call_id: string; tool_name: string; arguments: Record<string, unknown> }>;
      content?: string;
      message_id: string;
    };
    expect(p.message_id).toBe('a2');
    expect(p.content).toBe('Let me check.');
    expect(p.tool_calls).toEqual([
      { call_id: 'c1', tool_name: 'get_weather', arguments: { city: 'Tokyo' } },
      { call_id: 'c2', tool_name: 'get_time', arguments: { tz: 'Asia/Tokyo' } },
    ]);
  });

  test('Non-JSON tool-call arguments preserved under _raw', () => {
    const out = translateMessage(
      {
        id: 'a3',
        role: 'assistant',
        toolCalls: [
          {
            id: 'c1',
            type: 'function',
            function: { name: 'noop', arguments: 'not-json' },
          },
        ],
      } as never,
      fixedNow,
    );
    const p = out[0]!.payload as {
      tool_calls: Array<{ arguments: Record<string, unknown> }>;
    };
    expect(p.tool_calls[0]!.arguments).toEqual({ _raw: 'not-json' });
  });

  test('ToolMessage → ToolResultReceived', () => {
    const out = translateMessage(
      {
        id: 't1',
        role: 'tool',
        content: '{"temperature_c":22}',
        toolCallId: 'c1',
        name: 'get_weather',
      } as never,
      fixedNow,
    );
    expect(out).toEqual([
      {
        type: 'ToolResultReceived',
        payload: {
          call_id: 'c1',
          tool_name: 'get_weather',
          result: '{"temperature_c":22}',
          message_id: 't1',
          created_at: '2026-05-05T12:00:00.000Z',
          timestamp: '2026-05-05T12:00:00.000Z',
        },
      },
    ]);
  });

  test('ReasoningMessage plaintext → AssistantThinkingGenerated (encrypted=false)', () => {
    const out = translateMessage(
      { id: 'r1', role: 'reasoning', content: 'thinking out loud' } as never,
      fixedNow,
    );
    expect(out).toHaveLength(1);
    expect(out[0]!.type).toBe('AssistantThinkingGenerated');
    expect(out[0]!.payload).toMatchObject({
      content: 'thinking out loud',
      encrypted: false,
      message_id: 'r1',
    });
  });

  test('ReasoningMessage encrypted-only → AssistantThinkingGenerated (encrypted=true)', () => {
    const out = translateMessage(
      { id: 'r2', role: 'reasoning', encryptedValue: 'opaque-blob' } as never,
      fixedNow,
    );
    expect(out[0]!.payload).toMatchObject({
      encrypted: true,
      message_id: 'r2',
    });
  });

  test('SystemMessage / DeveloperMessage → skipped (no canonical events in v1)', () => {
    expect(
      translateMessage(
        { id: 's1', role: 'system', content: 'system prompt' } as never,
        fixedNow,
      ),
    ).toEqual([]);
    expect(
      translateMessage(
        { id: 'd1', role: 'developer', content: 'dev prompt' } as never,
        fixedNow,
      ),
    ).toEqual([]);
  });

  test('Message without id is defensively skipped', () => {
    expect(translateMessage({ role: 'user', content: 'no id' } as never, fixedNow)).toEqual(
      [],
    );
  });
});
