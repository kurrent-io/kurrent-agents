/**
 * Standalone writer for `ag-ui/interop-tests/`.
 *
 * Runs the middleware against a synthetic agent on the given session id
 * and exits when the stream is complete. Invoked from Python pytest as:
 *
 *   tsx test/interop-writer.ts <session-id> [run-id]
 *
 * The Python test then reads the resulting AgentSession-{session-id}
 * stream via `kurrent-agent-schema` and asserts each event parses to the
 * expected canonical type with matching field values.
 *
 * Keeping this here (rather than in ag-ui/interop-tests/) avoids a
 * separate TS package whose only dep would be this one.
 */

import {
  AbstractAgent,
  EventType,
  type BaseEvent,
  type RunAgentInput,
} from '@ag-ui/client';
import { KurrentDBClient } from '@kurrent/kurrentdb-client';
import { Observable } from 'rxjs';

import { KurrentDBMiddleware } from '../src/middleware.js';

const conn =
  process.env.KURRENTDB_CONNECTION_STRING ?? 'kurrentdb://localhost:2113?Tls=false';
const sessionId = process.argv[2];
const runId = process.argv[3] ?? `run-${Math.random().toString(36).slice(2, 10)}`;

if (!sessionId) {
  // eslint-disable-next-line no-console
  console.error('Usage: tsx interop-writer.ts <session-id> [run-id]');
  process.exit(2);
}

class FakeAgent extends AbstractAgent {
  constructor(threadId: string, private readonly events: BaseEvent[]) {
    super({ threadId, agentId: 'interop-fake', description: 'interop fake agent' });
  }

  run(_input: RunAgentInput): Observable<BaseEvent> {
    return new Observable<BaseEvent>((subscriber) => {
      for (const e of this.events) subscriber.next(e);
      subscriber.complete();
    });
  }
}

const ts = (): number => Date.now();

const events: BaseEvent[] = [
  { type: EventType.RUN_STARTED, threadId: sessionId, runId, timestamp: ts() } as never,
  { type: EventType.TEXT_MESSAGE_START, messageId: 'u1', role: 'user', timestamp: ts() } as never,
  { type: EventType.TEXT_MESSAGE_CONTENT, messageId: 'u1', delta: 'Weather in Oslo?', timestamp: ts() } as never,
  { type: EventType.TEXT_MESSAGE_END, messageId: 'u1', timestamp: ts() } as never,
  { type: EventType.TEXT_MESSAGE_START, messageId: 'a1', role: 'assistant', timestamp: ts() } as never,
  { type: EventType.TEXT_MESSAGE_CONTENT, messageId: 'a1', delta: 'Looking up.', timestamp: ts() } as never,
  { type: EventType.TEXT_MESSAGE_END, messageId: 'a1', timestamp: ts() } as never,
  { type: EventType.TOOL_CALL_START, toolCallId: 'c1', toolCallName: 'get_weather', parentMessageId: 'a1', timestamp: ts() } as never,
  { type: EventType.TOOL_CALL_ARGS, toolCallId: 'c1', delta: '{"city":"Oslo"}', timestamp: ts() } as never,
  { type: EventType.TOOL_CALL_END, toolCallId: 'c1', timestamp: ts() } as never,
  { type: EventType.TOOL_CALL_RESULT, messageId: 't1', toolCallId: 'c1', content: '{"temperature_c":8,"condition":"light_rain"}', role: 'tool', timestamp: ts() } as never,
  { type: EventType.TEXT_MESSAGE_START, messageId: 'a2', role: 'assistant', timestamp: ts() } as never,
  { type: EventType.TEXT_MESSAGE_CONTENT, messageId: 'a2', delta: '8°C with light rain.', timestamp: ts() } as never,
  { type: EventType.TEXT_MESSAGE_END, messageId: 'a2', timestamp: ts() } as never,
  { type: EventType.RUN_FINISHED, threadId: sessionId, runId, timestamp: ts() } as never,
];

const client = KurrentDBClient.connectionString(conn);
const agent = new FakeAgent(sessionId, events);
agent.use(new KurrentDBMiddleware({ client, appName: 'interop', agentName: 'fake' }));

await agent.runAgent({ runId });
// eslint-disable-next-line no-console
console.log(JSON.stringify({ sessionId, runId }));
process.exit(0);
