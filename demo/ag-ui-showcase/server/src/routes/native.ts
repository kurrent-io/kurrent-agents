/**
 * Generic AG-UI HTTP/SSE endpoint for the native lanes (MAF / ADK /
 * Strands). Each lane has its own /agent/{name} route registered
 * separately; the body of the route is identical, parameterised by
 * the framework.
 *
 * Flow:
 *   1. Read AG-UI RunAgentInput from the request body.
 *   2. Spawn runners/{lane}/runner.py — it writes canonical events to
 *      AgentSession-{threadId} as the (real or dummy) agent runs.
 *   3. Subscribe to that stream via DEV-1559 KurrentDBReplayAgent in
 *      `live` mode; pipe each AG-UI BaseEvent it emits to the SSE
 *      response.
 *   4. When the runner exits AND replay sees SessionEnded → close.
 */

import { EventType, type BaseEvent } from '@ag-ui/client';
import { KurrentDBReplayAgent } from '@kurrent-io/ag-ui-agent-kurrentdb-replay';
import { type KurrentDBClient } from '@kurrent/kurrentdb-client';
import { type FastifyInstance, type FastifyReply, type FastifyRequest } from 'fastify';

import { startRunner, type NativeFramework } from '../runner.js';

interface NativeRouteConfig {
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

export function registerNativeRoute(
  app: FastifyInstance,
  framework: NativeFramework,
  config: NativeRouteConfig,
): void {
  const log = config.logger ?? ((line) => app.log.info(line));

  app.post(`/agent/${framework}`, async (req: FastifyRequest, reply: FastifyReply): Promise<void> => {
    const body = req.body as RunAgentInputBody;
    if (!body || !body.threadId || !body.runId || !Array.isArray(body.messages)) {
      reply.code(400).send({ error: 'expected AG-UI RunAgentInput with threadId, runId, messages' });
      return;
    }

    const lastUser = [...body.messages].reverse().find((m) => m.role === 'user');
    if (!lastUser || !lastUser.content) {
      reply.code(400).send({ error: 'no user message in RunAgentInput.messages' });
      return;
    }

    reply.raw.setHeader('Content-Type', 'text/event-stream');
    reply.raw.setHeader('Cache-Control', 'no-cache');
    reply.raw.setHeader('Connection', 'keep-alive');
    reply.raw.flushHeaders();

    const sessionId = body.threadId;
    const send = (event: BaseEvent): void => {
      reply.raw.write(`data: ${JSON.stringify(event)}\n\n`);
    };

    const handle = startRunner({
      framework,
      sessionId,
      userMessage: lastUser.content,
      connectionString: config.connectionString,
      dummyMode: config.dummyMode,
      anthropicApiKey: config.anthropicApiKey,
      logger: log,
    });

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
        log(`[${framework}] replay error: ${(err as Error).message}`);
        try {
          send({
            type: EventType.RUN_ERROR,
            message: (err as Error).message,
          } as BaseEvent);
        } catch {
          /* ignore */
        }
        reply.raw.end();
      },
      complete: () => {
        if (!runFinishedSeen) {
          send({ type: EventType.RUN_FINISHED, threadId: sessionId, runId: body.runId } as BaseEvent);
        }
        reply.raw.end();
      },
    });

    // If the runner exits with a non-zero code (or exits cleanly but
    // the replay never sees SessionEnded within a grace period), we
    // synthesise a closing event so the SSE doesn't hang. Without
    // this, a runner that crashes before writing SessionEnded leaves
    // the browser waiting indefinitely.
    const FINALISE_GRACE_MS = 5_000;
    let finaliseTimer: ReturnType<typeof setTimeout> | undefined;

    handle.done.then((code) => {
      runnerExited = true;
      log(`[${framework}] runner exited (code=${code})`);
      if (code !== 0) {
        // Hard failure — emit RUN_ERROR and close immediately.
        if (!runFinishedSeen) {
          try {
            send({
              type: EventType.RUN_ERROR,
              message: `Runner exited with code ${code}`,
            } as BaseEvent);
          } catch {
            /* ignore */
          }
        }
        finalise();
        return;
      }
      // Clean exit. Give the replay subscription a brief window to
      // surface the SessionEnded → RUN_FINISHED that the runner just
      // wrote. If it doesn't arrive, synthesise one and close.
      if (runFinishedSeen) {
        finalise();
      } else {
        finaliseTimer = setTimeout(() => {
          if (!runFinishedSeen) {
            try {
              send({
                type: EventType.RUN_FINISHED,
                threadId: sessionId,
                runId: body.runId,
              } as BaseEvent);
            } catch {
              /* ignore */
            }
          }
          finalise();
        }, FINALISE_GRACE_MS);
      }
    });

    reply.raw.on('close', () => {
      if (finaliseTimer) clearTimeout(finaliseTimer);
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
      if (runnerExited && runFinishedSeen) {
        finalise();
      }
    }

    function finalise(): void {
      if (finaliseTimer) clearTimeout(finaliseTimer);
      subscription.unsubscribe();
      try {
        reply.raw.end();
      } catch {
        /* already ended */
      }
    }

    await new Promise<void>((resolve) => {
      reply.raw.once('close', resolve);
    });
  });
}
