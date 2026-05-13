import { EventType } from '@ag-ui/client';
import { describe, expect, test } from 'vitest';

import { makeContext, translateCanonical } from '../src/translator.js';

describe('translateCanonical', () => {
  test('SessionStarted → RUN_STARTED', () => {
    const ctx = makeContext('thr', 'r1');
    const out = translateCanonical('SessionStarted', { timestamp: 't' }, ctx);
    expect(out).toEqual([
      { type: EventType.RUN_STARTED, threadId: 'thr', runId: 'r1' },
    ]);
  });

  test('SessionEnded → RUN_FINISHED, idempotent', () => {
    const ctx = makeContext('thr', 'r1');
    const first = translateCanonical('SessionEnded', { timestamp: 't' }, ctx);
    expect(first).toEqual([
      { type: EventType.RUN_FINISHED, threadId: 'thr', runId: 'r1' },
    ]);
    expect(translateCanonical('SessionEnded', { timestamp: 't' }, ctx)).toEqual([]);
  });

  test('UserMessageReceived → TEXT_MESSAGE_*(role=user)', () => {
    const ctx = makeContext('thr', 'r1');
    const out = translateCanonical(
      'UserMessageReceived',
      { content: 'hi', message_id: 'u1', timestamp: 't' },
      ctx,
    );
    expect(out.map((e) => e.type)).toEqual([
      EventType.TEXT_MESSAGE_START,
      EventType.TEXT_MESSAGE_CONTENT,
      EventType.TEXT_MESSAGE_END,
    ]);
    expect((out[0] as { role: string }).role).toBe('user');
    expect((out[0] as { messageId: string }).messageId).toBe('u1');
    expect((out[1] as { delta: string }).delta).toBe('hi');
  });

  test('AssistantTextGenerated tracks lastAssistantMessageId', () => {
    const ctx = makeContext('thr', 'r1');
    translateCanonical(
      'AssistantTextGenerated',
      { content: 'hello', message_id: 'a1', timestamp: 't' },
      ctx,
    );
    expect(ctx.lastAssistantMessageId).toBe('a1');
  });

  test('AssistantThinkingGenerated plaintext → REASONING_MESSAGE_*', () => {
    const ctx = makeContext('thr', 'r1');
    const out = translateCanonical(
      'AssistantThinkingGenerated',
      { content: 'thinking', message_id: 'r1', timestamp: 't', encrypted: false },
      ctx,
    );
    expect(out.map((e) => e.type)).toEqual([
      EventType.REASONING_MESSAGE_START,
      EventType.REASONING_MESSAGE_CONTENT,
      EventType.REASONING_MESSAGE_END,
    ]);
  });

  test('AssistantThinkingGenerated encrypted-no-content → skipped', () => {
    const ctx = makeContext('thr', 'r1');
    const out = translateCanonical(
      'AssistantThinkingGenerated',
      { encrypted: true, message_id: 'r1', timestamp: 't' },
      ctx,
    );
    expect(out).toEqual([]);
  });

  test('AssistantToolCallsGenerated emits carrier text + per-call triple', () => {
    const ctx = makeContext('thr', 'r1');
    const out = translateCanonical(
      'AssistantToolCallsGenerated',
      {
        message_id: 'a1',
        content: 'Looking up.',
        tool_calls: [
          { call_id: 'c1', tool_name: 'get_weather', arguments: { city: 'Oslo' } },
        ],
        timestamp: 't',
      },
      ctx,
    );
    const types = out.map((e) => e.type);
    expect(types).toEqual([
      EventType.TEXT_MESSAGE_START,
      EventType.TEXT_MESSAGE_CONTENT,
      EventType.TEXT_MESSAGE_END,
      EventType.TOOL_CALL_START,
      EventType.TOOL_CALL_ARGS,
      EventType.TOOL_CALL_END,
    ]);
    const start = out[3] as {
      toolCallId: string;
      toolCallName: string;
      parentMessageId: string;
    };
    expect(start.toolCallId).toBe('c1');
    expect(start.toolCallName).toBe('get_weather');
    expect(start.parentMessageId).toBe('a1');
    expect(JSON.parse((out[4] as { delta: string }).delta)).toEqual({ city: 'Oslo' });
  });

  test('AssistantToolCallsGenerated without carrier emits only tool-call triple', () => {
    const ctx = makeContext('thr', 'r1');
    const out = translateCanonical(
      'AssistantToolCallsGenerated',
      {
        message_id: 'a1',
        tool_calls: [{ call_id: 'c1', tool_name: 'noop', arguments: {} }],
        timestamp: 't',
      },
      ctx,
    );
    expect(out.map((e) => e.type)).toEqual([
      EventType.TOOL_CALL_START,
      EventType.TOOL_CALL_ARGS,
      EventType.TOOL_CALL_END,
    ]);
    expect((out[1] as { delta: string }).delta).toBe('{}');
  });

  test('ToolResultReceived → TOOL_CALL_RESULT', () => {
    const ctx = makeContext('thr', 'r1');
    const out = translateCanonical(
      'ToolResultReceived',
      {
        call_id: 'c1',
        tool_name: 'get_weather',
        result: '{"temperature_c":8}',
        message_id: 't1',
        timestamp: 't',
      },
      ctx,
    );
    expect(out).toHaveLength(1);
    const e = out[0] as { type: string; toolCallId: string; content: string; role: string };
    expect(e.type).toBe(EventType.TOOL_CALL_RESULT);
    expect(e.toolCallId).toBe('c1');
    expect(JSON.parse(e.content)).toEqual({ temperature_c: 8 });
    expect(e.role).toBe('tool');
  });

  test('Unknown canonical event → CUSTOM (canonical.<type>) by default', () => {
    const ctx = makeContext('thr', 'r1');
    const out = translateCanonical('UnknownThing', { foo: 'bar' }, ctx);
    expect(out).toEqual([
      { type: EventType.CUSTOM, name: 'canonical.UnknownThing', value: { foo: 'bar' } },
    ]);
  });

  test('Unknown canonical event dropped when passthrough disabled', () => {
    const ctx = makeContext('thr', 'r1');
    ctx.customPassthrough = false;
    expect(translateCanonical('UnknownThing', {}, ctx)).toEqual([]);
  });

  test('Interrupt + Subagent + SessionContinuedAs → CUSTOM', () => {
    const ctx = makeContext('thr', 'r1');
    expect(
      translateCanonical('InterruptIssued', { request_id: 'x' }, ctx)[0],
    ).toMatchObject({ type: EventType.CUSTOM, name: 'interrupt.issued' });
    expect(
      translateCanonical('InterruptResolved', { request_id: 'x' }, ctx)[0],
    ).toMatchObject({ type: EventType.CUSTOM, name: 'interrupt.resolved' });
    expect(
      translateCanonical('SubagentStarted', { agent_id: 'x' }, ctx)[0],
    ).toMatchObject({ type: EventType.CUSTOM, name: 'subagent.started' });
    expect(
      translateCanonical('SubagentCompleted', { agent_id: 'x' }, ctx)[0],
    ).toMatchObject({ type: EventType.CUSTOM, name: 'subagent.completed' });
    expect(
      translateCanonical('SessionContinuedAs', { next_session_id: 'y' }, ctx)[0],
    ).toMatchObject({ type: EventType.CUSTOM, name: 'session.continued_as' });
  });

  test('Full session round-trip', () => {
    const ctx = makeContext('s1', 'run-1');
    const seq: Array<[string, Record<string, unknown>]> = [
      ['SessionStarted', { timestamp: 't' }],
      [
        'UserMessageReceived',
        { content: 'Weather in Oslo?', message_id: 'u1', timestamp: 't' },
      ],
      [
        'AssistantToolCallsGenerated',
        {
          message_id: 'a1',
          content: 'Looking up.',
          tool_calls: [
            { call_id: 'c1', tool_name: 'get_weather', arguments: { city: 'Oslo' } },
          ],
          timestamp: 't',
        },
      ],
      [
        'ToolResultReceived',
        { call_id: 'c1', tool_name: 'get_weather', result: '{"t":8}', message_id: 't1', timestamp: 't' },
      ],
      [
        'AssistantTextGenerated',
        { content: '8°C in Oslo.', message_id: 'a2', timestamp: 't' },
      ],
      ['SessionEnded', { timestamp: 't' }],
    ];
    const out = seq.flatMap(([t, p]) => translateCanonical(t, p, ctx));
    expect(out.map((e) => e.type)).toEqual([
      EventType.RUN_STARTED,
      EventType.TEXT_MESSAGE_START,
      EventType.TEXT_MESSAGE_CONTENT,
      EventType.TEXT_MESSAGE_END,
      EventType.TEXT_MESSAGE_START,
      EventType.TEXT_MESSAGE_CONTENT,
      EventType.TEXT_MESSAGE_END,
      EventType.TOOL_CALL_START,
      EventType.TOOL_CALL_ARGS,
      EventType.TOOL_CALL_END,
      EventType.TOOL_CALL_RESULT,
      EventType.TEXT_MESSAGE_START,
      EventType.TEXT_MESSAGE_CONTENT,
      EventType.TEXT_MESSAGE_END,
      EventType.RUN_FINISHED,
    ]);
  });
});
