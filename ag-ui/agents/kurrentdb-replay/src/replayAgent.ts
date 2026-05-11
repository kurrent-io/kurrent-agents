/**
 * KurrentDBReplayAgent — DEV-1559.
 *
 * AG-UI `AbstractAgent` that reads a canonical `AgentSession-{id}` stream
 * from KurrentDB and emits AG-UI events. Lets any AG-UI client (CopilotKit,
 * AG-UI Dojo, custom UI) render a session captured by *any* writer in the
 * monorepo — native MAF / ADK / Strands integrations OR the DEV-1558
 * middleware capturing AG-UI agents.
 *
 * Replay is **message-grained**: each canonical event becomes a complete
 * `*_START → _CONTENT/_ARGS → _END` triple in a single tick. We do not
 * fabricate token deltas (the canonical schema doesn't carry them).
 *
 * Modes:
 * - **catchup** (default) — read from the start to the end, emit, finish.
 * - **live** — catch up first, then subscribe to live appends and keep
 *   emitting AG-UI events as new canonical events land. The Observable
 *   does not complete until the consumer unsubscribes or `RUN_FINISHED`
 *   is emitted from a `SessionEnded`.
 */

import {
  AbstractAgent,
  EventType,
  type BaseEvent,
  type RunAgentInput,
} from '@ag-ui/client';
import {
  FORWARDS,
  START,
  type KurrentDBClient,
} from '@kurrent/kurrentdb-client';
import { Observable } from 'rxjs';

import { agentSessionStream } from './streamNames.js';
import { makeContext, translateCanonical } from './translator.js';

export type ReplayMode = 'catchup' | 'live';

export interface KurrentDBReplayAgentConfig {
  client: KurrentDBClient;
  /** Canonical session id (becomes AG-UI threadId). */
  sessionId: string;
  /** catchup (default) or live tail. */
  mode?: ReplayMode;
  /** Optional max events to read in catchup mode. */
  maxCount?: number;
  /** Whether unknown canonical event types emit CUSTOM events. Default true. */
  customPassthrough?: boolean;
  /** Optional logger for diagnostics. */
  logger?: (msg: string) => void;
}

export class KurrentDBReplayAgent extends AbstractAgent {
  private readonly client: KurrentDBClient;
  private readonly sessionId: string;
  private readonly mode: ReplayMode;
  private readonly maxCount?: number;
  private readonly customPassthrough: boolean;
  private readonly log: (msg: string) => void;

  constructor(config: KurrentDBReplayAgentConfig) {
    super({
      threadId: config.sessionId,
      agentId: 'kurrentdb-replay',
      description: 'Replays a canonical KurrentDB AgentSession as AG-UI events',
    });
    this.client = config.client;
    this.sessionId = config.sessionId;
    this.mode = config.mode ?? 'catchup';
    this.maxCount = config.maxCount;
    this.customPassthrough = config.customPassthrough ?? true;
    this.log = config.logger ?? (() => undefined);
  }

  run(input: RunAgentInput): Observable<BaseEvent> {
    return new Observable<BaseEvent>((subscriber) => {
      const ctx = makeContext(input.threadId ?? this.sessionId, input.runId);
      ctx.customPassthrough = this.customPassthrough;
      const stream = agentSessionStream(this.sessionId);
      let cancelled = false;
      // Track unique messageIds emitted so live appends don't replay
      // canonical events the catchup phase already emitted (read-after-
      // write or appends arriving while we're still iterating catchup).
      const emittedMessageIds = new Set<string>();

      const emitFromCanonical = (
        type: string,
        data: Record<string, unknown>,
      ): void => {
        if (cancelled || ctx.finished) return;
        // Cheap dedup at the canonical-event level: if a canonical
        // event carries a message_id we've already turned into AG-UI
        // events, skip it. (Catch-up + live overlap, primarily.)
        const id = data.message_id as string | undefined;
        if (id && emittedMessageIds.has(id)) return;
        for (const ag of translateCanonical(type, data, ctx)) {
          subscriber.next(ag);
        }
        if (id) emittedMessageIds.add(id);
      };

      const finish = (): void => {
        if (ctx.finished) {
          subscriber.complete();
          return;
        }
        // Synthesise RUN_FINISHED if the stream didn't carry SessionEnded.
        // Without it, AG-UI consumers wait forever.
        ctx.finished = true;
        subscriber.next({
          type: EventType.RUN_FINISHED,
          threadId: ctx.threadId,
          runId: ctx.runId,
        } as BaseEvent);
        subscriber.complete();
      };

      const runCatchup = async (): Promise<void> => {
        try {
          const events = this.client.readStream(stream, {
            direction: FORWARDS,
            fromRevision: START,
            ...(this.maxCount !== undefined ? { maxCount: this.maxCount } : {}),
          });
          for await (const resolved of events) {
            if (cancelled) return;
            const e = resolved.event;
            if (!e) continue;
            const data = (e.data ?? {}) as Record<string, unknown>;
            emitFromCanonical(e.type, data);
          }
        } catch (err) {
          // StreamNotFound is fine — empty session, nothing to replay.
          if ((err as { type?: string })?.type !== 'stream-not-found') {
            this.log(`[kurrentdb-replay] catchup failed: ${(err as Error).message}`);
            subscriber.error(err);
            return;
          }
        }
      };

      const runLive = async (): Promise<void> => {
        // KurrentDB subscribeToStream from START gives catch-up + live
        // in one consistent ordering. We dedup by messageId so the
        // catch-up window is harmless.
        try {
          const subscription = this.client.subscribeToStream(stream, {
            fromRevision: START,
          });
          for await (const resolved of subscription) {
            if (cancelled) {
              subscription.unsubscribe?.();
              return;
            }
            const e = resolved.event;
            if (!e) continue;
            const data = (e.data ?? {}) as Record<string, unknown>;
            emitFromCanonical(e.type, data);
            if (ctx.finished) {
              subscription.unsubscribe?.();
              subscriber.complete();
              return;
            }
          }
        } catch (err) {
          this.log(`[kurrentdb-replay] live subscription error: ${(err as Error).message}`);
          subscriber.error(err);
        }
      };

      (async (): Promise<void> => {
        if (this.mode === 'catchup') {
          await runCatchup();
          if (!cancelled) finish();
        } else {
          await runLive();
        }
      })().catch((err) => {
        if (!cancelled) subscriber.error(err);
      });

      return () => {
        cancelled = true;
      };
    });
  }
}
