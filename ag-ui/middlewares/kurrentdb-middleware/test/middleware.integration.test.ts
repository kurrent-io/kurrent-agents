/**
 * End-to-end test against a live KurrentDB instance.
 *
 * Spins up a synthetic AG-UI agent that emits a canned event sequence
 * (RUN_STARTED → message build-up via TEXT/TOOL events → RUN_FINISHED),
 * wraps it with KurrentDBMiddleware, runs it, then reads back the
 * AgentSession-* stream and asserts the canonical events landed.
 *
 * Requires `demo/docker-compose.yml` running on :2113.
 */

import {
  AbstractAgent,
  EventType,
  type BaseEvent,
  type RunAgentInput,
} from '@ag-ui/client';
import {
  KurrentDBClient,
  FORWARDS,
  START,
} from '@kurrent/kurrentdb-client';
import { Observable } from 'rxjs';
import { describe, expect, test, beforeAll, afterAll } from 'vitest';

import { KurrentDBMiddleware } from '../src/middleware.js';
import { agentSessionStream } from '../src/streamNames.js';

const CONN =
  process.env.KURRENTDB_CONNECTION_STRING ?? 'kurrentdb://localhost:2113?Tls=false';

let client: KurrentDBClient;

beforeAll(() => {
  client = KurrentDBClient.connectionString(CONN);
});

afterAll(async () => {
  // KurrentDB TS client doesn't expose an explicit close in v1; relying
  // on process exit. If/when an explicit dispose API lands, call it here.
});

class FakeAgent extends AbstractAgent {
  constructor(threadId: string, private readonly events: BaseEvent[]) {
    super({ threadId, agentId: 'test-agent', description: 'test agent' });
  }

  run(_input: RunAgentInput): Observable<BaseEvent> {
    return new Observable<BaseEvent>((subscriber) => {
      for (const e of this.events) subscriber.next(e);
      subscriber.complete();
    });
  }
}

function ts(): number {
  return Date.now();
}

describe('KurrentDBMiddleware (live KurrentDB)', () => {
  test('persists a two-turn AG-UI session as canonical events', async () => {
    const threadId = `agui-mw-${Math.random().toString(36).slice(2, 10)}`;
    const runId = `run-${Math.random().toString(36).slice(2, 10)}`;

    // Synthesise a run: user asks weather → assistant calls tool → tool
    // result → assistant final answer. Built using TEXT_MESSAGE_*/TOOL_CALL_*
    // events the way a real framework would emit them.
    const events: BaseEvent[] = [
      { type: EventType.RUN_STARTED, threadId, runId, timestamp: ts() } as never,

      // user message
      { type: EventType.TEXT_MESSAGE_START, messageId: 'u1', role: 'user', timestamp: ts() } as never,
      { type: EventType.TEXT_MESSAGE_CONTENT, messageId: 'u1', delta: 'Weather in Tokyo?', timestamp: ts() } as never,
      { type: EventType.TEXT_MESSAGE_END, messageId: 'u1', timestamp: ts() } as never,

      // assistant emits text + tool call
      { type: EventType.TEXT_MESSAGE_START, messageId: 'a1', role: 'assistant', timestamp: ts() } as never,
      { type: EventType.TEXT_MESSAGE_CONTENT, messageId: 'a1', delta: 'Let me check.', timestamp: ts() } as never,
      { type: EventType.TEXT_MESSAGE_END, messageId: 'a1', timestamp: ts() } as never,
      { type: EventType.TOOL_CALL_START, toolCallId: 'c1', toolCallName: 'get_weather', parentMessageId: 'a1', timestamp: ts() } as never,
      { type: EventType.TOOL_CALL_ARGS, toolCallId: 'c1', delta: '{"city":"Tokyo"}', timestamp: ts() } as never,
      { type: EventType.TOOL_CALL_END, toolCallId: 'c1', timestamp: ts() } as never,

      // tool result
      { type: EventType.TOOL_CALL_RESULT, messageId: 't1', toolCallId: 'c1', content: '{"temperature_c":22}', role: 'tool', timestamp: ts() } as never,

      // assistant final answer
      { type: EventType.TEXT_MESSAGE_START, messageId: 'a2', role: 'assistant', timestamp: ts() } as never,
      { type: EventType.TEXT_MESSAGE_CONTENT, messageId: 'a2', delta: '22°C in Tokyo.', timestamp: ts() } as never,
      { type: EventType.TEXT_MESSAGE_END, messageId: 'a2', timestamp: ts() } as never,

      { type: EventType.RUN_FINISHED, threadId, runId, timestamp: ts() } as never,
    ];

    const agent = new FakeAgent(threadId, events);
    const middleware = new KurrentDBMiddleware({
      client,
      appName: 'test-app',
      agentName: 'test-agent',
    });
    agent.use(middleware);

    await agent.runAgent({ runId });

    // Read the AgentSession-* stream and assert the canonical events.
    const collected: Array<{ type: string; data: Record<string, unknown>; metadata: Record<string, unknown> }> = [];
    const stream = client.readStream(agentSessionStream(threadId), {
      direction: FORWARDS,
      fromRevision: START,
      maxCount: 64,
    });
    for await (const resolved of stream) {
      const e = resolved.event!;
      // KurrentDB TS client auto-parses JSON events: e.data and e.metadata
      // are already objects.
      collected.push({
        type: e.type,
        data: e.data as Record<string, unknown>,
        metadata: (e.metadata ?? {}) as Record<string, unknown>,
      });
    }

    const types = collected.map((e) => e.type);
    expect(types).toEqual([
      'SessionStarted',
      'UserMessageReceived',
      'AssistantToolCallsGenerated',
      'ToolResultReceived',
      'AssistantTextGenerated',
      'SessionEnded',
    ]);

    // SessionStarted carries appName / agentName.
    expect(collected[0]!.data).toMatchObject({
      app_name: 'test-app',
      agent_name: 'test-agent',
    });
    // SessionStarted does NOT carry model when none is configured (synthetic
    // FakeAgent has no model; AgentConfig is empty since FakeAgent.run is
    // called with an input that has no tools/forwardedProps).
    expect(collected[0]!.data).not.toHaveProperty('model');
    expect(collected[0]!.data).not.toHaveProperty('agent_config');

    // User message landed verbatim.
    expect(collected[1]!.data).toMatchObject({
      content: 'Weather in Tokyo?',
      message_id: 'u1',
    });

    // Tool call grouped under one event with content as carrier text.
    const toolCallsEvent = collected[2]!.data as {
      content?: string;
      message_id: string;
      tool_calls: Array<{ call_id: string; tool_name: string; arguments: Record<string, unknown> }>;
    };
    expect(toolCallsEvent.message_id).toBe('a1');
    expect(toolCallsEvent.content).toBe('Let me check.');
    expect(toolCallsEvent.tool_calls).toEqual([
      { call_id: 'c1', tool_name: 'get_weather', arguments: { city: 'Tokyo' } },
    ]);

    // Tool result.
    expect(collected[3]!.data).toMatchObject({
      call_id: 'c1',
      result: '{"temperature_c":22}',
    });

    // Final assistant text.
    expect(collected[4]!.data).toMatchObject({
      content: '22°C in Tokyo.',
      message_id: 'a2',
    });

    // SessionEnded normal completion.
    expect(collected[5]!.data).toMatchObject({ reason: 'complete' });

    // runId stamped on every event's metadata.
    for (const e of collected) {
      expect((e.metadata as Record<string, unknown>).$run_id).toBe(runId);
      expect((e.metadata as Record<string, unknown>).$schema_version).toBe(2);
    }
  }, 30_000);

  test('SessionStarted carries model + AgentConfig when middleware can derive them', async () => {
    const threadId = `agui-mw-cfg-${Math.random().toString(36).slice(2, 10)}`;
    const runId = `run-${Math.random().toString(36).slice(2, 10)}`;

    // Subclass FakeAgent so its run() receives a RunAgentInput with
    // tools/forwardedProps populated. AbstractAgent.runAgent() builds
    // the input from the parameters we pass.
    class FakeAgentWithTools extends AbstractAgent {
      constructor(private readonly events: BaseEvent[]) {
        super({ threadId, agentId: 'cfg-fake', description: 'fake' });
      }
      run(_: RunAgentInput): Observable<BaseEvent> {
        return new Observable<BaseEvent>((sub) => {
          for (const e of this.events) sub.next(e);
          sub.complete();
        });
      }
    }
    const events: BaseEvent[] = [
      { type: EventType.RUN_STARTED, threadId, runId, timestamp: ts() } as never,
      { type: EventType.RUN_FINISHED, threadId, runId, timestamp: ts() } as never,
    ];

    const agent = new FakeAgentWithTools(events);
    agent.use(
      new KurrentDBMiddleware({
        client,
        appName: 'cfg',
        // explicit model wins over context/forwardedProps lookup
        model: 'claude-haiku-4-5',
      }),
    );
    await agent.runAgent({
      runId,
      tools: [
        {
          name: 'get_weather',
          description: 'Look up the weather for a city',
          parameters: { type: 'object', properties: { city: { type: 'string' } } },
        },
      ],
      forwardedProps: { temperature: 0.7 },
    });

    const stream = client.readStream(agentSessionStream(threadId), {
      direction: FORWARDS,
      fromRevision: START,
      maxCount: 4,
    });
    let sessionStartedData: Record<string, unknown> | undefined;
    for await (const resolved of stream) {
      const e = resolved.event!;
      if (e.type === 'SessionStarted') {
        sessionStartedData = e.data as Record<string, unknown>;
        break;
      }
    }
    expect(sessionStartedData).toBeDefined();
    expect(sessionStartedData!.model).toBe('claude-haiku-4-5');
    const cfg = sessionStartedData!.agent_config as {
      tools?: Array<{ name: string; description?: string; source?: string }>;
      model_parameters?: Record<string, unknown>;
    };
    expect(cfg.tools).toEqual([
      expect.objectContaining({
        name: 'get_weather',
        description: 'Look up the weather for a city',
        source: 'ag_ui',
      }),
    ]);
    expect(cfg.model_parameters).toEqual({ temperature: 0.7 });
  }, 30_000);

  test('onStateEvent hook fires for STATE_SNAPSHOT / STATE_DELTA / MESSAGES_SNAPSHOT', async () => {
    const threadId = `agui-mw-state-${Math.random().toString(36).slice(2, 10)}`;
    const runId = `run-${Math.random().toString(36).slice(2, 10)}`;

    const events: BaseEvent[] = [
      { type: EventType.RUN_STARTED, threadId, runId, timestamp: ts() } as never,
      { type: EventType.STATE_SNAPSHOT, snapshot: { count: 0 }, timestamp: ts() } as never,
      { type: EventType.STATE_DELTA, delta: [{ op: 'replace', path: '/count', value: 1 }], timestamp: ts() } as never,
      { type: EventType.MESSAGES_SNAPSHOT, messages: [], timestamp: ts() } as never,
      { type: EventType.RUN_FINISHED, threadId, runId, timestamp: ts() } as never,
    ];

    const agent = new FakeAgent(threadId, events);
    const captured: string[] = [];
    agent.use(
      new KurrentDBMiddleware({
        client,
        onStateEvent: ({ event }) => {
          captured.push(event.type as string);
        },
      }),
    );
    await agent.runAgent({ runId });

    expect(captured).toEqual(['STATE_SNAPSHOT', 'STATE_DELTA', 'MESSAGES_SNAPSHOT']);

    // Side-effect: state events do NOT land in the canonical stream
    // (DEV-1562 will add that). Only SessionStarted + SessionEnded.
    const types: string[] = [];
    const stream = client.readStream(agentSessionStream(threadId), {
      direction: FORWARDS,
      fromRevision: START,
      maxCount: 16,
    });
    for await (const resolved of stream) {
      types.push(resolved.event!.type);
    }
    expect(types).toEqual(['SessionStarted', 'SessionEnded']);
  }, 30_000);

  test('idempotent re-run does not double-write user messages', async () => {
    const threadId = `agui-mw-idem-${Math.random().toString(36).slice(2, 10)}`;
    const runId1 = `r-${Math.random().toString(36).slice(2, 10)}`;
    const runId2 = `r-${Math.random().toString(36).slice(2, 10)}`;

    const buildEvents = (rid: string): BaseEvent[] => [
      { type: EventType.RUN_STARTED, threadId, runId: rid, timestamp: ts() } as never,
      { type: EventType.TEXT_MESSAGE_START, messageId: 'u1', role: 'user', timestamp: ts() } as never,
      { type: EventType.TEXT_MESSAGE_CONTENT, messageId: 'u1', delta: 'hi', timestamp: ts() } as never,
      { type: EventType.TEXT_MESSAGE_END, messageId: 'u1', timestamp: ts() } as never,
      { type: EventType.RUN_FINISHED, threadId, runId: rid, timestamp: ts() } as never,
    ];

    // First run: writes SessionStarted + UserMessageReceived + SessionEnded.
    const a1 = new FakeAgent(threadId, buildEvents(runId1));
    a1.use(new KurrentDBMiddleware({ client }));
    await a1.runAgent({ runId: runId1 });

    // Second run with the same `u1` message id: middleware should skip
    // the duplicate user message but still record SessionEnded.
    const a2 = new FakeAgent(threadId, buildEvents(runId2));
    a2.use(new KurrentDBMiddleware({ client }));
    await a2.runAgent({ runId: runId2 });

    const types: string[] = [];
    const stream2 = client.readStream(agentSessionStream(threadId), {
      direction: FORWARDS,
      fromRevision: START,
      maxCount: 64,
    });
    for await (const resolved of stream2) {
      types.push(resolved.event!.type);
    }
    // Only one UserMessageReceived; SessionStarted only on the first run
    // (second run sees stream exists, skips).
    expect(types.filter((t) => t === 'UserMessageReceived')).toHaveLength(1);
    expect(types.filter((t) => t === 'SessionStarted')).toHaveLength(1);
    expect(types.filter((t) => t === 'SessionEnded')).toHaveLength(2);
  }, 30_000);
});
