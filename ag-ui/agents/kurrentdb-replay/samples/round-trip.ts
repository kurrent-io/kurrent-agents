/**
 * Round-trip demo: AG-UI events → middleware → KurrentDB → replay → AG-UI events.
 *
 * Uses the DEV-1558 middleware to capture a synthetic AG-UI agent's output,
 * then turns around and uses DEV-1559 (this package) to replay the same
 * session. Side-by-side print of the original AG-UI events and the
 * replayed events shows the round-trip and where it differs (chunky
 * replay: each canonical message becomes one START/_CONTENT/_END).
 *
 * Prereq: KurrentDB on :2113 (`cd demo && docker compose up -d`).
 *
 * Run:
 *   cd ag-ui/agents/kurrentdb-replay
 *   npm install
 *   npm run sample
 */

import {
  AbstractAgent,
  EventType,
  type BaseEvent,
  type RunAgentInput,
} from '@ag-ui/client';
import { KurrentDBMiddleware } from '@kurrent-io/ag-ui-middleware-kurrentdb';
import { KurrentDBClient } from '@kurrent/kurrentdb-client';
import { firstValueFrom, Observable, toArray } from 'rxjs';

import { KurrentDBReplayAgent } from '../src/replayAgent.js';

const CONN =
  process.env.KURRENTDB_CONNECTION_STRING ?? 'kurrentdb://localhost:2113?Tls=false';

class FakeAgent extends AbstractAgent {
  constructor(threadId: string, private readonly events: BaseEvent[]) {
    super({ threadId, agentId: 'roundtrip-fake', description: 'roundtrip fake' });
  }
  run(_: RunAgentInput): Observable<BaseEvent> {
    return new Observable<BaseEvent>((sub) => {
      for (const e of this.events) sub.next(e);
      sub.complete();
    });
  }
}

const ts = (): number => Date.now();

function describe(e: BaseEvent): string {
  const x = e as Record<string, unknown>;
  switch (e.type) {
    case EventType.RUN_STARTED:
      return `RUN_STARTED            run=${x.runId}`;
    case EventType.RUN_FINISHED:
      return `RUN_FINISHED           run=${x.runId}`;
    case EventType.TEXT_MESSAGE_START:
      return `TEXT_MESSAGE_START     id=${x.messageId} role=${x.role}`;
    case EventType.TEXT_MESSAGE_CONTENT:
      return `TEXT_MESSAGE_CONTENT   id=${x.messageId} delta=${JSON.stringify(x.delta)}`;
    case EventType.TEXT_MESSAGE_END:
      return `TEXT_MESSAGE_END       id=${x.messageId}`;
    case EventType.TOOL_CALL_START:
      return `TOOL_CALL_START        id=${x.toolCallId} name=${x.toolCallName} parent=${x.parentMessageId}`;
    case EventType.TOOL_CALL_ARGS:
      return `TOOL_CALL_ARGS         id=${x.toolCallId} delta=${x.delta}`;
    case EventType.TOOL_CALL_END:
      return `TOOL_CALL_END          id=${x.toolCallId}`;
    case EventType.TOOL_CALL_RESULT:
      return `TOOL_CALL_RESULT       id=${x.toolCallId} content=${x.content}`;
    default:
      return String(e.type);
  }
}

async function main(): Promise<void> {
  const sessionId = `roundtrip-${Math.random().toString(36).slice(2, 10)}`;
  const writeRunId = `write-${Math.random().toString(36).slice(2, 10)}`;
  const replayRunId = `replay-${Math.random().toString(36).slice(2, 10)}`;
  const client = KurrentDBClient.connectionString(CONN);

  // -------- 1. originating AG-UI agent emits 15 events --------
  const original: BaseEvent[] = [
    { type: EventType.RUN_STARTED, threadId: sessionId, runId: writeRunId, timestamp: ts() } as never,
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
    { type: EventType.RUN_FINISHED, threadId: sessionId, runId: writeRunId, timestamp: ts() } as never,
  ];

  console.log(`=== Original AG-UI events (${original.length}, written by ${writeRunId}) ===\n`);
  for (const e of original) console.log('  ' + describe(e));

  // Drive through the middleware → KurrentDB.
  const writer = new FakeAgent(sessionId, original);
  writer.use(new KurrentDBMiddleware({ client, appName: 'roundtrip', agentName: 'demo' }));
  await writer.runAgent({ runId: writeRunId });

  // -------- 2. replay agent reads the same session and emits AG-UI --------
  const replayer = new KurrentDBReplayAgent({ client, sessionId });
  const replayed = await firstValueFrom(
    replayer.run({ threadId: sessionId, runId: replayRunId } as never).pipe(toArray()),
  );

  console.log(`\n=== Replayed AG-UI events (${replayed.length}, by ${replayRunId}) ===\n`);
  for (const e of replayed) console.log('  ' + describe(e));

  console.log(
    `\n${original.length} → 6 canonical events on disk → ${replayed.length} replayed events. ` +
      `Same shape, different runId, same conversation.`,
  );
}

main()
  .then(() => process.exit(0))
  .catch((err) => {
    console.error(err);
    process.exit(1);
  });
