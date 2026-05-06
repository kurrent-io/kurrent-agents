/**
 * Side-by-side demo: AG-UI events emitted by an agent vs. canonical
 * events persisted by KurrentDBMiddleware to KurrentDB.
 *
 * Mirrors the shape of `microsoft-agent-framework/python/samples/basic_roundtrip.py`:
 * a synthetic agent (no LLM, no API key) emits a canned AG-UI event
 * sequence, the middleware translates and appends, and we read the
 * resulting AgentSession-* stream to show what was preserved.
 *
 * The point: ~14 incremental AG-UI events collapse to 6 canonical
 * events that any framework integration in this monorepo can replay.
 *
 * Prereq: KurrentDB on :2113 (`cd demo && docker compose up -d`).
 *
 * Run:
 *   cd ag-ui/middlewares/kurrentdb-middleware
 *   npm install
 *   npm run sample
 */

import {
  AbstractAgent,
  EventType,
  type BaseEvent,
  type RunAgentInput,
} from '@ag-ui/client';
import { FORWARDS, KurrentDBClient, START } from '@kurrent/kurrentdb-client';
import { Observable } from 'rxjs';

import { KurrentDBMiddleware } from '../src/middleware.js';
import { agentSessionStream } from '../src/streamNames.js';

const CONN =
  process.env.KURRENTDB_CONNECTION_STRING ?? 'kurrentdb://localhost:2113?Tls=false';

class FakeAgent extends AbstractAgent {
  constructor(threadId: string, private readonly events: BaseEvent[]) {
    super({ threadId, agentId: 'sample-agent', description: 'sample weather agent' });
  }

  run(_input: RunAgentInput): Observable<BaseEvent> {
    return new Observable<BaseEvent>((subscriber) => {
      for (const e of this.events) subscriber.next(e);
      subscriber.complete();
    });
  }
}

const ts = (): number => Date.now();

function buildEvents(threadId: string, runId: string): BaseEvent[] {
  return [
    { type: EventType.RUN_STARTED, threadId, runId, timestamp: ts() } as never,

    { type: EventType.TEXT_MESSAGE_START, messageId: 'u1', role: 'user', timestamp: ts() } as never,
    { type: EventType.TEXT_MESSAGE_CONTENT, messageId: 'u1', delta: "What's the weather in Oslo?", timestamp: ts() } as never,
    { type: EventType.TEXT_MESSAGE_END, messageId: 'u1', timestamp: ts() } as never,

    { type: EventType.TEXT_MESSAGE_START, messageId: 'a1', role: 'assistant', timestamp: ts() } as never,
    { type: EventType.TEXT_MESSAGE_CONTENT, messageId: 'a1', delta: 'Looking that up.', timestamp: ts() } as never,
    { type: EventType.TEXT_MESSAGE_END, messageId: 'a1', timestamp: ts() } as never,
    { type: EventType.TOOL_CALL_START, toolCallId: 'c1', toolCallName: 'get_weather', parentMessageId: 'a1', timestamp: ts() } as never,
    { type: EventType.TOOL_CALL_ARGS, toolCallId: 'c1', delta: '{"city":"Oslo"}', timestamp: ts() } as never,
    { type: EventType.TOOL_CALL_END, toolCallId: 'c1', timestamp: ts() } as never,

    { type: EventType.TOOL_CALL_RESULT, messageId: 't1', toolCallId: 'c1', content: '{"temperature_c":8,"condition":"light_rain"}', role: 'tool', timestamp: ts() } as never,

    { type: EventType.TEXT_MESSAGE_START, messageId: 'a2', role: 'assistant', timestamp: ts() } as never,
    { type: EventType.TEXT_MESSAGE_CONTENT, messageId: 'a2', delta: '8°C with light rain in Oslo.', timestamp: ts() } as never,
    { type: EventType.TEXT_MESSAGE_END, messageId: 'a2', timestamp: ts() } as never,

    { type: EventType.RUN_FINISHED, threadId, runId, timestamp: ts() } as never,
  ];
}

function describeAGUIEvent(e: BaseEvent): string {
  const t = e.type as string;
  const x = e as Record<string, unknown>;
  switch (t) {
    case 'RUN_STARTED':
      return `RUN_STARTED            thread=${x.threadId} run=${x.runId}`;
    case 'RUN_FINISHED':
      return `RUN_FINISHED           thread=${x.threadId} run=${x.runId}`;
    case 'RUN_ERROR':
      return `RUN_ERROR              ${x.message ?? ''}`;
    case 'TEXT_MESSAGE_START':
      return `TEXT_MESSAGE_START     id=${x.messageId} role=${x.role}`;
    case 'TEXT_MESSAGE_CONTENT':
      return `TEXT_MESSAGE_CONTENT   id=${x.messageId} delta=${JSON.stringify(x.delta)}`;
    case 'TEXT_MESSAGE_END':
      return `TEXT_MESSAGE_END       id=${x.messageId}`;
    case 'TOOL_CALL_START':
      return `TOOL_CALL_START        id=${x.toolCallId} name=${x.toolCallName} parent=${x.parentMessageId}`;
    case 'TOOL_CALL_ARGS':
      return `TOOL_CALL_ARGS         id=${x.toolCallId} delta=${x.delta}`;
    case 'TOOL_CALL_END':
      return `TOOL_CALL_END          id=${x.toolCallId}`;
    case 'TOOL_CALL_RESULT':
      return `TOOL_CALL_RESULT       id=${x.toolCallId} content=${x.content}`;
    default:
      return `${t}`;
  }
}

function describeCanonical(type: string, data: Record<string, unknown>): string {
  switch (type) {
    case 'SessionStarted':
      return `SessionStarted              app_name=${data.app_name} agent_name=${data.agent_name}`;
    case 'UserMessageReceived':
      return `UserMessageReceived         id=${data.message_id} content=${JSON.stringify(data.content)}`;
    case 'AssistantTextGenerated':
      return `AssistantTextGenerated      id=${data.message_id} content=${JSON.stringify(data.content)}`;
    case 'AssistantToolCallsGenerated': {
      const calls = data.tool_calls as Array<{ call_id: string; tool_name: string; arguments: Record<string, unknown> }>;
      const carrier = data.content ? ` carrier=${JSON.stringify(data.content)}` : '';
      const callDescs = calls.map((c) => `${c.tool_name}(${JSON.stringify(c.arguments)})`).join(', ');
      return `AssistantToolCallsGenerated id=${data.message_id}${carrier} calls=[${callDescs}]`;
    }
    case 'ToolResultReceived':
      return `ToolResultReceived          call_id=${data.call_id} result=${data.result}`;
    case 'SessionEnded':
      return `SessionEnded                reason=${data.reason}`;
    default:
      return type;
  }
}

async function main(): Promise<void> {
  const threadId = `sample-${Math.random().toString(36).slice(2, 10)}`;
  const runId = `run-${Math.random().toString(36).slice(2, 10)}`;

  const client = KurrentDBClient.connectionString(CONN);

  // 1. Tee the AG-UI event stream we're about to send so we can also
  //    print it. This is the *input* to the middleware.
  const events = buildEvents(threadId, runId);
  console.log(`=== AG-UI events emitted by agent (${events.length}) ===\n`);
  for (const e of events) console.log('  ' + describeAGUIEvent(e));
  console.log('');

  // 2. Run the agent through the middleware. Persistence is a
  //    side-effect; outer subscribers (none here) would see the
  //    AG-UI events untouched.
  const agent = new FakeAgent(threadId, events);
  agent.use(
    new KurrentDBMiddleware({
      client,
      appName: 'weather_demo',
      agentName: 'WeatherAgent',
    }),
  );
  await agent.runAgent({ runId });

  // 3. Read back what landed in KurrentDB.
  console.log(`=== Canonical events persisted to ${agentSessionStream(threadId)} ===\n`);
  let i = 0;
  const stream = client.readStream(agentSessionStream(threadId), {
    direction: FORWARDS,
    fromRevision: START,
    maxCount: 64,
  });
  for await (const resolved of stream) {
    const e = resolved.event;
    if (!e) continue;
    const data = e.data as Record<string, unknown>;
    const meta = (e.metadata ?? {}) as Record<string, unknown>;
    const runIdMeta = meta.$run_id ? ` [$run_id=${meta.$run_id}]` : '';
    console.log(`  [${i}] ${describeCanonical(e.type, data)}${runIdMeta}`);
    i++;
  }
  console.log(
    `\n${events.length} AG-UI events → ${i} canonical events. ` +
      `Any Python or .NET integration in this monorepo can now replay this session.`,
  );
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
