/**
 * POST /agent/maf — AG-UI HTTP/SSE endpoint.
 *
 * Flow:
 *   1. Read AG-UI RunAgentInput from the request body.
 *   2. Spawn maf_runner.py — it writes canonical events to
 *      AgentSession-{threadId} as the (real or dummy) MAF agent runs.
 *   3. Subscribe to that stream via DEV-1559 KurrentDBReplayAgent in
 *      `live` mode; pipe each AG-UI BaseEvent it emits to the SSE
 *      response.
 *   4. When the runner exits AND replay sees SessionEnded → close.
 *
 * The browser sees real-time message-grained AG-UI events. Each
 * canonical event becomes one START/_CONTENT-or-_ARGS/_END triple.
 */

import { EventType, type BaseEvent } from '@ag-ui/client';
import { KurrentDBReplayAgent } from '@kurrent-io/ag-ui-agent-kurrentdb-replay';
import { type KurrentDBClient } from '@kurrent/kurrentdb-client';
import { type FastifyInstance, type FastifyReply, type FastifyRequest } from 'fastify';

import { startRunner } from '../runner.js';

interface MafRouteConfig {
  client: KurrentDBClient;
  connectionString: string;
  dummyMode: boolean;
  anthropicApiKey?: string;
  logger?: (line: string) => void;
}

interface RunAgentInputBody {
  threadId: string;
  runId: string;
  messages: Array<{ role: string; content?: string; id?: string }>;
}

export function registerMafRoute(app: FastifyInstance, config: MafRouteConfig): void {
  const log = config.logger ?? ((line) => app.log.info(line));

  app.post('/agent/maf', async (req: FastifyRequest, reply: FastifyReply): Promise<void> => {
    const body = req.body as RunAgentInputBody;
    if (!body || !body.threadId || !body.runId || !Array.isArray(body.messages)) {
      reply.code(400).send({ error: 'expected AG-UI RunAgentInput with threadId, runId, messages' });
      return;
    }

    // Pull the latest user message from the input. The client is expected
    // to send every prior turn for context, but on the demo's write side
    // we only need the new message — the canonical history is already in
    // KurrentDB.
    const lastUser = [...body.messages].reverse().find((m) => m.role === 'user');
    if (!lastUser || !lastUser.content) {
      reply.code(400).send({ error: 'no user message in RunAgentInput.messages' });
      return;
    }

    // SSE prelude.
    reply.raw.setHeader('Content-Type', 'text/event-stream');
    reply.raw.setHeader('Cache-Control', 'no-cache');
    reply.raw.setHeader('Connection', 'keep-alive');
    reply.raw.flushHeaders();

    const sessionId = body.threadId;
    const send = (event: BaseEvent): void => {
      reply.raw.write(`data: ${JSON.stringify(event)}\n\n`);
    };

    // 1. Spawn the Python runner. It will write canonical events as
    //    the agent makes progress.
    const handle = startRunner({
      framework: 'maf',
      sessionId,
      userMessage: lastUser.content,
      connectionString: config.connectionString,
      dummyMode: config.dummyMode,
      anthropicApiKey: config.anthropicApiKey,
      logger: log,
    });

    // 2. Subscribe to the stream via DEV-1559 in live mode. The replay
    //    agent will catch up (any prior history) and live-tail new
    //    appends from the runner. We dedup new events from old by
    //    starting a new "replay run" — the AG-UI runId on the wire is
    //    the request's runId, separate from the canonical $run_id.
    const replay = new KurrentDBReplayAgent({
      client: config.client,
      sessionId,
      runId: body.runId,
      mode: 'live',
    });
    const obs = replay.run({ threadId: sessionId, runId: body.runId } as never);

    let runnerExited = false;
    let runFinishedSeen = false;

    const subscription = obs.subscribe({
      next: (ev: BaseEvent) => {
        send(ev);
        if (ev.type === EventType.RUN_FINISHED || ev.type === EventType.RUN_ERROR) {
          runFinishedSeen = true;
          maybeFinalise();
        }
      },
      error: (err) => {
        log(`[maf] replay error: ${(err as Error).message}`);
        try {
          send({
            type: EventType.RUN_ERROR,
            message: (err as Error).message,
          } as BaseEvent);
        } catch {
          /* ignore double-write */
        }
        reply.raw.end();
      },
      complete: () => {
        if (!runFinishedSeen) {
          // Replay closed without seeing SessionEnded — emit a safety
          // RUN_FINISHED.
          send({ type: EventType.RUN_FINISHED, threadId: sessionId, runId: body.runId } as BaseEvent);
        }
        reply.raw.end();
      },
    });

    handle.done.then((code) => {
      runnerExited = true;
      log(`[maf] runner exited (code=${code})`);
      maybeFinalise();
    });

    // Browser dropped: cancel everything. Listen on the *response* socket
    // — `req.raw.on('close')` fires when Fastify finishes parsing the
    // request body (within ~30ms), which would kill the runner before
    // it produces anything. The response socket only closes when the
    // client genuinely disconnects.
    reply.raw.on('close', () => {
      if (!runnerExited) {
        try {
          handle.process.kill();
        } catch {
          /* ignore */
        }
      }
      subscription.unsubscribe();
    });

    function maybeFinalise(): void {
      // Close once we've seen RUN_FINISHED *and* the runner has exited.
      // Either alone is not enough: replay could see SessionEnded then
      // we wait for the Python process to clean up; or the process can
      // exit but the last event is still flushing through the
      // subscription.
      if (runnerExited && runFinishedSeen) {
        subscription.unsubscribe();
        reply.raw.end();
      }
    }

    // Keep Fastify happy — returning a Promise that resolves when the
    // raw stream ends.
    await new Promise<void>((resolve) => {
      reply.raw.once('close', resolve);
    });
  });
}
