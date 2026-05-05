/**
 * Per-thread idempotency tracker.
 *
 * AG-UI's `RunAgentInput.messages` and `resume[]` mean the middleware can
 * see a user message that's already in the canonical stream from a prior
 * run. Dedup strategy (per ag-ui/GAPS.md §4.3): on first session touch,
 * read the existing AgentSession-* stream tail and seed an in-memory
 * `messageId` set; new messages are written and added to the set.
 */

import { END, FORWARDS, START, type KurrentDBClient } from '@kurrent/kurrentdb-client';

import { agentSessionStream } from './streamNames.js';

export class MessageIdDedup {
  private readonly seen = new Map<string, Set<string>>();
  private readonly initialised = new Set<string>();

  /** Idempotently load already-persisted messageIds for a session. */
  async ensureInitialised(client: KurrentDBClient, sessionId: string): Promise<void> {
    if (this.initialised.has(sessionId)) return;
    this.initialised.add(sessionId);
    const set = new Set<string>();
    this.seen.set(sessionId, set);
    try {
      const events = client.readStream(agentSessionStream(sessionId), {
        direction: FORWARDS,
        fromRevision: START,
        maxCount: 4096,
      });
      for await (const resolved of events) {
        const e = resolved.event;
        if (!e) continue;
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
      // StreamNotFound is fine — first run for this session.
      if ((err as { type?: string })?.type !== 'stream-not-found') throw err;
    }
    // Touch unused imports.
    void END;
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
}
