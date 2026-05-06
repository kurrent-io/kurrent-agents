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
  type AgentConfig,
  type CanonicalEvent,
  type SessionStarted,
  type ToolSpec,
} from './types.js';

/**
 * State observation hook reserved for DEV-1562. The middleware does not
 * persist state events in v1; subscribers attach via this callback to
 * observe `STATE_SNAPSHOT`, `STATE_DELTA`, and `MESSAGES_SNAPSHOT` as
 * the agent emits them.
 *
 * Each call carries the AG-UI event itself plus the post-event accumulated
 * state and message list (via @ag-ui/client's runNextWithState).
 */
export type StateEventHook = (input: {
  event: BaseEvent;
  state: unknown;
  messages: Message[];
  sessionId: string;
  runId: string;
}) => void | Promise<void>;

export interface KurrentDBMiddlewareOptions {
  client: KurrentDBClient;
  /** Map an AG-UI thread to a canonical session id. Defaults to threadId. */
  scope?: (input: RunAgentInput) => string;
  /** Optional `app_name` stamped on `SessionStarted`. */
  appName?: string;
  /** Optional `agent_name` stamped on `SessionStarted`. */
  agentName?: string;
  /**
   * Optional `model` for `SessionStarted`. Supply directly, or as a
   * function pulling from `RunAgentInput` (e.g. from `forwardedProps`
   * or `context`).
   */
  model?: string | ((input: RunAgentInput) => string | undefined);
  /**
   * Override how `AgentConfig` is derived from `RunAgentInput`. Default
   * fills `tools` from `RunAgentInput.tools` (mapped to canonical
   * `ToolSpec`) and `forwardedProps` into `model_parameters` when
   * present.
   */
  agentConfig?:
    | AgentConfig
    | ((input: RunAgentInput) => AgentConfig | undefined);
  /**
   * Reserved for DEV-1562. Called for every `STATE_SNAPSHOT` /
   * `STATE_DELTA` / `MESSAGES_SNAPSHOT` event the inner agent emits.
   * Default: no-op. The middleware itself does not persist state in v1.
   */
  onStateEvent?: StateEventHook;
  /** Optional logger; receives single-line strings. */
  logger?: (msg: string) => void;
}

export class KurrentDBMiddleware extends Middleware {
  private readonly client: KurrentDBClient;
  private readonly scope: (input: RunAgentInput) => string;
  private readonly appName?: string;
  private readonly agentName?: string;
  private readonly resolveModel: (input: RunAgentInput) => string | undefined;
  private readonly resolveAgentConfig: (input: RunAgentInput) => AgentConfig | undefined;
  private readonly onStateEvent?: StateEventHook;
  private readonly log: (msg: string) => void;
  private readonly dedup = new MessageIdDedup();

  constructor(opts: KurrentDBMiddlewareOptions) {
    super();
    this.client = opts.client;
    this.scope = opts.scope ?? ((input) => input.threadId);
    this.appName = opts.appName;
    this.agentName = opts.agentName;
    this.resolveModel =
      typeof opts.model === 'function'
        ? opts.model
        : opts.model !== undefined
          ? () => opts.model as string
          : (input) => extractModel(input);
    this.resolveAgentConfig =
      typeof opts.agentConfig === 'function'
        ? opts.agentConfig
        : opts.agentConfig !== undefined
          ? () => opts.agentConfig as AgentConfig
          : (input) => deriveAgentConfig(input);
    this.onStateEvent = opts.onStateEvent;
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
            model: this.resolveModel(input),
            agent_config: this.resolveAgentConfig(input),
            timestamp: ts,
          };
          // Drop undefined keys for a clean wire payload.
          if (payload.model === undefined) delete payload.model;
          if (payload.agent_config === undefined) delete payload.agent_config;
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
        next: ({ event, messages, state }) => {
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
            case EventType.STATE_SNAPSHOT:
            case EventType.STATE_DELTA:
            case EventType.MESSAGES_SNAPSHOT:
              if (this.onStateEvent) {
                // Reserve a place on the pipeline so the hook completes
                // before SessionEnded fires, but errors don't fail the
                // run — DEV-1562 will decide policy.
                enqueue(async () => {
                  try {
                    await this.onStateEvent!({
                      event,
                      state,
                      messages,
                      sessionId,
                      runId: runId ?? '',
                    });
                  } catch (err) {
                    this.log(
                      `[kurrentdb-middleware] onStateEvent threw: ${(err as Error).message}`,
                    );
                  }
                });
              }
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
  return findContextValue(input, 'user_id');
}

function extractModel(input: RunAgentInput): string | undefined {
  // Prefer forwardedProps.model when set (most frameworks stash it there);
  // fall back to a `model` context entry.
  const fp = (input as { forwardedProps?: Record<string, unknown> }).forwardedProps;
  if (fp && typeof fp.model === 'string') return fp.model;
  return findContextValue(input, 'model');
}

function findContextValue(input: RunAgentInput, description: string): string | undefined {
  const ctx = (input.context ?? []) as Array<{ description?: string; value?: unknown }>;
  for (const c of ctx) {
    if (c.description === description && typeof c.value === 'string') return c.value;
  }
  return undefined;
}

/**
 * Default `AgentConfig` derivation: map `RunAgentInput.tools` to
 * canonical `ToolSpec`, lift `forwardedProps` (minus `model`, which
 * lands on `SessionStarted.model`) into `model_parameters`. Returns
 * `undefined` when the input has no useful agent metadata.
 */
function deriveAgentConfig(input: RunAgentInput): AgentConfig | undefined {
  const tools = input.tools ?? [];
  const fp = ({ ...((input as { forwardedProps?: Record<string, unknown> }).forwardedProps ?? {}) } as Record<string, unknown>);
  delete fp.model;

  const config: AgentConfig = {};
  if (tools.length > 0) {
    config.tools = tools.map<ToolSpec>((t) => ({
      name: t.name,
      description: t.description,
      input_schema: t.parameters,
      source: 'ag_ui',
    }));
  }
  if (Object.keys(fp).length > 0) {
    config.model_parameters = fp;
  }
  return Object.keys(config).length > 0 ? config : undefined;
}
