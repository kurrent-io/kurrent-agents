/**
 * End-to-end test: write canonical events directly to KurrentDB, then
 * `KurrentDBReplayAgent.run()` and assert the AG-UI event sequence.
 *
 * This is the symmetric counterpart of the DEV-1558 middleware test —
 * write side proven there, read side proven here.
 *
 * Requires KurrentDB on :2113.
 */

import { EventType, type BaseEvent } from '@ag-ui/client';
import {
  ANY,
  KurrentDBClient,
  jsonEvent,
} from '@kurrent/kurrentdb-client';
import { firstValueFrom, toArray } from 'rxjs';
import { describe, expect, test } from 'vitest';

import { KurrentDBReplayAgent } from '../src/replayAgent.js';
import { agentSessionStream } from '../src/streamNames.js';

const CONN =
  process.env.KURRENTDB_CONNECTION_STRING ?? 'kurrentdb://localhost:2113?Tls=false';

function rid(prefix: string): string {
  return `${prefix}-${Math.random().toString(36).slice(2, 10)}`;
}

interface CanonicalAppend {
  type: string;
  data: Record<string, unknown>;
}

async function writeCanonical(
  client: KurrentDBClient,
  sessionId: string,
  events: CanonicalAppend[],
): Promise<void> {
  for (const e of events) {
    await client.appendToStream(
      agentSessionStream(sessionId),
      jsonEvent({
        type: e.type,
        data: e.data,
        metadata: { $schema_version: 2 },
      }),
      { streamState: ANY },
    );
  }
}

const ts = (): string => new Date().toISOString();

describe('KurrentDBReplayAgent (live KurrentDB)', () => {
  test('replays a six-event canonical session as AG-UI events', async () => {
    const client = KurrentDBClient.connectionString(CONN);
    const sessionId = rid('replay');

    await writeCanonical(client, sessionId, [
      {
        type: 'SessionStarted',
        data: { app_name: 'demo', agent_name: 'WeatherAgent', timestamp: ts() },
      },
      {
        type: 'UserMessageReceived',
        data: { content: "What's the weather in Oslo?", message_id: 'u1', timestamp: ts() },
      },
      {
        type: 'AssistantToolCallsGenerated',
        data: {
          message_id: 'a1',
          content: 'Looking up.',
          tool_calls: [
            { call_id: 'c1', tool_name: 'get_weather', arguments: { city: 'Oslo' } },
          ],
          timestamp: ts(),
        },
      },
      {
        type: 'ToolResultReceived',
        data: {
          call_id: 'c1',
          tool_name: 'get_weather',
          result: '{"temperature_c":8,"condition":"light_rain"}',
          message_id: 't1',
          timestamp: ts(),
        },
      },
      {
        type: 'AssistantTextGenerated',
        data: { content: '8°C with light rain in Oslo.', message_id: 'a2', timestamp: ts() },
      },
      {
        type: 'SessionEnded',
        data: { reason: 'complete', timestamp: ts() },
      },
    ]);

    const runId = rid('run');
    const agent = new KurrentDBReplayAgent({ client, sessionId });
    const collected = await firstValueFrom(
      agent.run({ threadId: sessionId, runId } as never).pipe(toArray()),
    );

    const types = collected.map((e: BaseEvent) => e.type);
    expect(types).toEqual([
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

    const runStarted = collected[0] as { threadId: string; runId: string };
    expect(runStarted.threadId).toBe(sessionId);
    expect(runStarted.runId).toBe(runId);

    const toolCallStart = collected.find((e) => e.type === EventType.TOOL_CALL_START) as {
      toolCallId: string;
      toolCallName: string;
      parentMessageId: string;
    };
    expect(toolCallStart.toolCallId).toBe('c1');
    expect(toolCallStart.toolCallName).toBe('get_weather');
    expect(toolCallStart.parentMessageId).toBe('a1');

    const toolCallArgs = collected.find((e) => e.type === EventType.TOOL_CALL_ARGS) as {
      delta: string;
    };
    expect(JSON.parse(toolCallArgs.delta)).toEqual({ city: 'Oslo' });

    const toolResult = collected.find((e) => e.type === EventType.TOOL_CALL_RESULT) as {
      toolCallId: string;
      content: string;
    };
    expect(toolResult.toolCallId).toBe('c1');
    expect(JSON.parse(toolResult.content)).toEqual({
      temperature_c: 8,
      condition: 'light_rain',
    });
  }, 30_000);

  test('non-existent session emits only RUN_STARTED-then-RUN_FINISHED safety closure', async () => {
    const client = KurrentDBClient.connectionString(CONN);
    const sessionId = rid('replay-empty');
    const runId = rid('run');
    const agent = new KurrentDBReplayAgent({ client, sessionId });
    const collected = await firstValueFrom(
      agent.run({ threadId: sessionId, runId } as never).pipe(toArray()),
    );
    // Empty stream → no SessionStarted in the canonical stream → no
    // RUN_STARTED is fabricated. The replay agent synthesises a
    // safety RUN_FINISHED so consumers don't hang.
    expect(collected.map((e) => e.type)).toEqual([EventType.RUN_FINISHED]);
  }, 30_000);
});
