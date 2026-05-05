/**
 * KurrentDB AG-UI middleware (DEV-1558).
 *
 * Wraps any AG-UI agent so its sessions persist as canonical events in
 * KurrentDB. A single TypeScript implementation captures sessions from
 * every framework that has an AG-UI integration (LangGraph, Mastra,
 * CrewAI, MAF, ADK, …) — see ag-ui/GAPS.md for the strategic framing.
 *
 * Persistence model
 * -----------------
 * AG-UI emits messages incrementally (TEXT_MESSAGE_START → CONTENT →
 * END, then TOOL_CALL_START → ARGS → END can attach tool calls to the
 * same assistant message after END). To avoid persisting an "assistant
 * text" canonical event prematurely and then being unable to upgrade it
 * when tool calls arrive, the middleware **defers persistence to
 * RUN_FINISHED / RUN_ERROR** — at that point each message is in its
 * final form. The price is "no partial durability" if the host process
 * dies mid-run; aborted runs are explicitly handled.
 *
 * Idempotency on resume — see ag-ui/GAPS.md §4.3.
 * runId stamping — see ag-ui/GAPS.md §4.1.
 * State events (STATE_SNAPSHOT/_DELTA) — DEV-1562, not in v1.
 */

import {
  EventType,
  Middleware,
  type AbstractAgent,
  type BaseEvent,
  type Message,
  type RunAgentInput,
} from '@ag-ui/client';
import { ANY, jsonEvent, type KurrentDBClient } from '@kurrent/kurrentdb-client';
import { Observable } from 'rxjs';

import { MessageIdDedup } from './dedup.js';
import { agentSessionStream } from './streamNames.js';
import { translateMessage } from './translator.js';
import {
  RUN_ID_METADATA_KEY,
  SCHEMA_VERSION,
  SCHEMA_VERSION_METADATA_KEY,
  type CanonicalEvent,
  type SessionStarted,
} from './types.js';

export interface KurrentDBMiddlewareOptions {
  client: KurrentDBClient;
  /** Map an AG-UI thread to a canonical session id. Defaults to threadId. */
  scope?: (input: RunAgentInput) => string;
  /** Optional `app_name` stamped on `SessionStarted`. */
  appName?: string;
  /** Optional `agent_name` stamped on `SessionStarted`. */
  agentName?: string;
  /** Optional logger; receives single-line strings. */
  logger?: (msg: string) => void;
}

export class KurrentDBMiddleware extends Middleware {
  private readonly client: KurrentDBClient;
  private readonly scope: (input: RunAgentInput) => string;
  private readonly appName?: string;
  private readonly agentName?: string;
  private readonly log: (msg: string) => void;
  private readonly dedup = new MessageIdDedup();

  constructor(opts: KurrentDBMiddlewareOptions) {
    super();
    this.client = opts.client;
    this.scope = opts.scope ?? ((input) => input.threadId);
    this.appName = opts.appName;
    this.agentName = opts.agentName;
    this.log = opts.logger ?? (() => undefined);
  }

  run(input: RunAgentInput, next: AbstractAgent): Observable<BaseEvent> {
    return new Observable<BaseEvent>((subscriber) => {
      const sessionId = this.scope(input);
      const runId = input.runId;
      const stream = agentSessionStream(sessionId);

      let latestMessages: Message[] = [];
      let aborted = false;
      let ended = false;

      // Single per-run pipeline so all KurrentDB writes are strictly
      // ordered. Prevents races between ensureSessionStarted and
      // message persistence.
      let pipeline: Promise<void> = Promise.resolve();
      const enqueue = (work: () => Promise<void>): void => {
        pipeline = pipeline.then(work).catch((err) => {
          this.log(`[kurrentdb-middleware] pipeline error: ${(err as Error).message}`);
          throw err;
        });
      };

      const persist = async (event: CanonicalEvent): Promise<void> => {
        const metadata: Record<string, unknown> = {
          [SCHEMA_VERSION_METADATA_KEY]: SCHEMA_VERSION,
        };
        if (runId) metadata[RUN_ID_METADATA_KEY] = runId;
        await this.client.appendToStream(
          stream,
          jsonEvent({
            type: event.type,
            data: event.payload as unknown as Record<string, unknown>,
            metadata,
          }),
          { streamState: ANY },
        );
      };

      const onRunStarted = async (): Promise<void> => {
        await this.dedup.ensureInitialised(this.client, sessionId);
        // SessionStarted is emitted only on the *first* run that ever
        // touches this session. Subsequent runs (resume) see existing
        // events and skip it.
        if (this.dedup.size(sessionId) === 0) {
          const ts = new Date().toISOString();
          const payload: SessionStarted = {
            app_name: this.appName,
            agent_name: this.agentName,
            user_id: extractUserId(input),
            timestamp: ts,
          };
          await persist({ type: 'SessionStarted', payload });
        }
      };

      const flushMessagesAndEnd = async (reason: string): Promise<void> => {
        if (ended) return;
        ended = true;
        for (const message of latestMessages) {
          const id = (message as { id?: string }).id;
          if (!id) continue;
          if (this.dedup.has(sessionId, id)) continue;
          this.dedup.add(sessionId, id);
          for (const event of translateMessage(message)) {
            await persist(event);
          }
        }
        const endTs = new Date().toISOString();
        await persist({
          type: 'SessionEnded',
          payload: {
            reason,
            timestamp: endTs,
            ...(aborted ? { extensions: { ag_ui: { aborted: true } } } : {}),
          },
        });
      };

      const inner = this.runNextWithState(input, next).subscribe({
        next: ({ event, messages }) => {
          subscriber.next(event);
          latestMessages = messages;

          switch (event.type) {
            case EventType.RUN_STARTED:
              enqueue(onRunStarted);
              break;
            case EventType.RUN_ERROR:
              aborted = true;
              enqueue(() => flushMessagesAndEnd('error'));
              break;
            case EventType.RUN_FINISHED:
              enqueue(() => flushMessagesAndEnd('complete'));
              break;
            default:
              break;
          }
        },
        error: (err) => {
          aborted = true;
          enqueue(() => flushMessagesAndEnd('error'));
          pipeline.finally(() => subscriber.error(err));
        },
        complete: () => {
          // Make sure we close out even if RUN_FINISHED was missing
          // (rare, but keeps the stream consistent).
          enqueue(async () => {
            if (!ended) await flushMessagesAndEnd('complete');
          });
          pipeline.then(
            () => subscriber.complete(),
            (err) => subscriber.error(err),
          );
        },
      });

      return () => inner.unsubscribe();
    });
  }
}

function extractUserId(input: RunAgentInput): string | undefined {
  const ctx = (input.context ?? []) as Array<{ description?: string; value?: unknown }>;
  for (const c of ctx) {
    if (c.description === 'user_id' && typeof c.value === 'string') return c.value;
  }
  return undefined;
}
