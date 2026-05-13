import { useCallback, useMemo, useRef, useState } from 'react';

import { Chat } from './components/Chat';
import {
  FRAMEWORK_ENABLED,
  FRAMEWORK_LABELS,
  type Framework,
  makeAgent,
} from './lib/agent';
import {
  applyEvent,
  emptyAccumulator,
  enqueueOptimisticUser,
  type MessageAccumulator,
  type TextBubble,
} from './lib/messages';

function newSessionId(): string {
  return `demo-${Math.random().toString(36).slice(2, 10)}`;
}

function newRunId(): string {
  return `run-${Math.random().toString(36).slice(2, 10)}`;
}

const KURRENTDB_ADMIN_URL = 'http://localhost:2113';

export function App(): JSX.Element {
  const [framework, setFramework] = useState<Framework>('maf');
  const [sessionId, setSessionId] = useState(newSessionId);
  const [acc, setAcc] = useState<MessageAccumulator>(emptyAccumulator());
  const [pending, setPending] = useState<string>('');
  const [running, setRunning] = useState(false);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const resetSession = useCallback(() => {
    setRunning(false);
    setAcc(emptyAccumulator());
    setSessionId(newSessionId());
  }, []);

  const handleSubmit = useCallback(async () => {
    const text = pending.trim();
    if (!text || running) return;

    // Optimistic user bubble — pre-render so the user sees their input
    // immediately. The accumulator queues the local id; when the
    // server echoes the same message via `TEXT_MESSAGE_*(role=user)`,
    // it claims this bubble (remaps id to the canonical messageId,
    // suppresses the now-redundant content deltas) instead of
    // rendering a duplicate.
    const userBubble: TextBubble = {
      kind: 'text',
      id: `local-user-${Date.now()}`,
      role: 'user',
      content: text,
      pending: false,
    };
    setAcc((prev) => enqueueOptimisticUser(prev, userBubble));
    setPending('');
    setRunning(true);

    const agent = makeAgent(framework, sessionId);
    // Build RunAgentInput inline so the request body has every field
    // the server expects. (Calling agent.run({runId}) sends only
    // {runId} — see AG-UI HttpAgent.requestInit using JSON.stringify.)
    const runId = newRunId();
    const input = {
      threadId: sessionId,
      runId,
      tools: [],
      context: [],
      forwardedProps: {},
      state: {},
      messages: [{ id: userBubble.id, role: 'user' as const, content: text }],
    };
    agent.run(input as never).subscribe({
      next: (event) => setAcc((prev) => applyEvent(prev, event)),
      error: (err) => {
        setAcc((prev) => ({
          ...prev,
          bubbles: [
            ...prev.bubbles,
            {
              kind: 'error',
              id: `err-${Date.now()}`,
              message: (err as Error).message ?? String(err),
            },
          ],
        }));
        setRunning(false);
      },
      complete: () => setRunning(false),
    });
  }, [framework, pending, running, sessionId]);

  const onKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        void handleSubmit();
      }
    },
    [handleSubmit],
  );

  const streamUrl = useMemo(
    () => `${KURRENTDB_ADMIN_URL}/web/index.html#/streams/AgentSession-${sessionId}`,
    [sessionId],
  );

  return (
    <div className="app">
      <header className="app-header">
        <h1>kurrent-agents · AG-UI showcase</h1>
        <div className="subtitle">
          One canonical schema. Pick a framework; same UI, same KurrentDB streams.
        </div>
        <div className="app-controls">
          <label htmlFor="framework">Framework:</label>
          <select
            id="framework"
            value={framework}
            onChange={(e) => setFramework(e.target.value as Framework)}
            disabled={running}
          >
            {(Object.keys(FRAMEWORK_LABELS) as Framework[]).map((f) => (
              <option key={f} value={f} disabled={!FRAMEWORK_ENABLED[f]}>
                {FRAMEWORK_LABELS[f]}
              </option>
            ))}
          </select>
          <button className="new-session" onClick={resetSession} disabled={running}>
            New session
          </button>
          <span className="session-id">{sessionId}</span>
          <a
            className="kdb-link"
            href={streamUrl}
            target="_blank"
            rel="noreferrer"
          >
            View stream in KurrentDB ↗
          </a>
        </div>
      </header>

      <Chat bubbles={acc.bubbles} thinking={running} />

      <div className="composer">
        <textarea
          ref={inputRef}
          value={pending}
          onChange={(e) => setPending(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Type a message…  (Shift+Enter for newline, Enter to send)"
          disabled={running}
        />
        <button onClick={handleSubmit} disabled={running || !pending.trim()}>
          Send
        </button>
      </div>
    </div>
  );
}
