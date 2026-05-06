import { useCallback, useMemo, useRef, useState } from 'react';
import type { Subscription } from 'rxjs';

import { Chat } from './components/Chat';
import {
  EventType,
  FRAMEWORK_ENABLED,
  FRAMEWORK_LABELS,
  type BaseEvent,
  type Framework,
  makeAgent,
} from './lib/agent';
import {
  applyEvent,
  emptyAccumulator,
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
  const subscriptionRef = useRef<Subscription | null>(null);

  const resetSession = useCallback(() => {
    subscriptionRef.current?.unsubscribe();
    subscriptionRef.current = null;
    setRunning(false);
    setAcc(emptyAccumulator());
    setSessionId(newSessionId());
  }, []);

  const handleSubmit = useCallback(async () => {
    const text = pending.trim();
    if (!text || running) return;

    // Optimistic user bubble — server will echo via UserMessageReceived
    // → TEXT_MESSAGE_*(role=user). We pre-add it here and let the
    // accumulator's de-dupe-by-messageId merge them.
    const userBubble: TextBubble = {
      kind: 'text',
      id: `local-user-${Date.now()}`,
      role: 'user',
      content: text,
      pending: false,
    };
    setAcc((prev) => ({
      ...prev,
      bubbles: [...prev.bubbles, userBubble],
    }));
    setPending('');
    setRunning(true);

    const agent = makeAgent(framework, sessionId);
    const runId = newRunId();
    const sub = agent
      .run({
        runId,
        // HttpAgent sends `messages` automatically from agent.messages,
        // so we mutate it here to include the new user turn.
        // The agent maintains the running history.
      } as never)
      .subscribe({
        next: (event: BaseEvent) => {
          setAcc((prev) => applyEvent(prev, event));
          if (event.type === EventType.RUN_FINISHED || event.type === EventType.RUN_ERROR) {
            setRunning(false);
          }
        },
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
    subscriptionRef.current = sub;

    // The HttpAgent doesn't quite know about our user message yet; push
    // it into agent.messages so it shows up in RunAgentInput.messages.
    agent.messages = [
      ...(agent.messages ?? []),
      { id: userBubble.id, role: 'user', content: text } as never,
    ];
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
                {!FRAMEWORK_ENABLED[f] ? ' (Phase 2)' : ''}
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
