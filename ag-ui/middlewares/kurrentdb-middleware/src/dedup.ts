/**
 * Per-thread idempotency tracker.
 *
 * AG-UI's `RunAgentInput.messages` and `resume[]` mean the middleware can
 * see a user message that's already in the canonical stream from a prior
 * run. Dedup strategy (per ag-ui/GAPS.md §4.3): on first session touch,
 * read the existing `AgentSession-*` stream forward from the start and
 * seed an in-memory `messageId` set; new messages are written and added
 * to the set as the run proceeds.
 *
 * The tracker also records whether the canonical stream existed at all
 * when first touched. The middleware uses that to decide whether to emit
 * `SessionStarted` — checking message-id count is unreliable because a
 * stream can exist with `SessionStarted` but no messages (e.g. a prior
 * crashed run).
 */

import { FORWARDS, START, type KurrentDBClient } from '@kurrent/kurrentdb-client';

import { agentSessionStream } from './streamNames.js';

export class MessageIdDedup {
  private readonly seen = new Map<string, Set<string>>();
  private readonly initialised = new Set<string>();
  private readonly streamExists = new Set<string>();

  /** Idempotently load already-persisted messageIds for a session. */
  async ensureInitialised(client: KurrentDBClient, sessionId: string): Promise<void> {
    if (this.initialised.has(sessionId)) return;
    const set = new Set<string>();
    let exists = false;
    try {
      // Read forward from the start to the end of the stream, no
      // maxCount cap. A cap would silently miss older message_ids on
      // long-lived sessions and let duplicate canonical messages
      // through on resume — correctness over startup latency.
      const events = client.readStream(agentSessionStream(sessionId), {
        direction: FORWARDS,
        fromRevision: START,
      });
      for await (const resolved of events) {
        const e = resolved.event;
        if (!e) continue;
        exists = true;
        // KurrentDB TS client auto-parses JSON events: e.data is already
        // an object for events written via jsonEvent. Decode bytes only
        // as a defensive fallback (binary or non-JSON events).
        let payload: unknown = e.data;
        if (payload instanceof Uint8Array) {
          try {
            payload = JSON.parse(new TextDecoder().decode(payload));
          } catch {
            payload = undefined;
          }
        }
        const id = (payload as { message_id?: string } | undefined)?.message_id;
        if (id) set.add(id);
      }
    } catch (err: unknown) {
      if ((err as { type?: string })?.type === 'stream-not-found') {
        // First-touch session — initialise as empty.
        this.seen.set(sessionId, set);
        this.initialised.add(sessionId);
        return;
      }
      // Transient failure: leave initialised flag *unset* so a later
      // call can retry. Don't pollute `seen`/`streamExists` either.
      throw err;
    }
    // Read succeeded — commit state.
    this.seen.set(sessionId, set);
    if (exists) this.streamExists.add(sessionId);
    this.initialised.add(sessionId);
  }

  has(sessionId: string, messageId: string): boolean {
    return this.seen.get(sessionId)?.has(messageId) ?? false;
  }

  add(sessionId: string, messageId: string): void {
    let set = this.seen.get(sessionId);
    if (!set) {
      set = new Set<string>();
      this.seen.set(sessionId, set);
    }
    set.add(messageId);
  }

  /** Number of messageIds currently tracked for a session. */
  size(sessionId: string): number {
    return this.seen.get(sessionId)?.size ?? 0;
  }

  /**
   * Whether the canonical `AgentSession-*` stream existed (had any
   * events) when this dedup was first initialised for the session.
   * Tracks a fact distinct from whether any messages had been seen.
   */
  hasExistingStream(sessionId: string): boolean {
    return this.streamExists.has(sessionId);
  }
}
