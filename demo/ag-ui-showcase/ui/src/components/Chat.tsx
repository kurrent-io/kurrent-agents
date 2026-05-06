import { useEffect, useRef } from 'react';

import type { Bubble } from '../lib/messages';

interface ChatProps {
  bubbles: Bubble[];
  thinking: boolean;
}

export function Chat({ bubbles, thinking }: ChatProps): JSX.Element {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = containerRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [bubbles, thinking]);

  return (
    <div className="chat" ref={containerRef}>
      {bubbles.length === 0 && !thinking && (
        <div className="empty-hint">
          Type a message below. The agent persists everything to KurrentDB; you'll see canonical events stream back as AG-UI.
        </div>
      )}
      {bubbles.map((b) => (
        <BubbleView key={b.id} bubble={b} />
      ))}
      {thinking && <div className="thinking">Agent is thinking…</div>}
    </div>
  );
}

function BubbleView({ bubble }: { bubble: Bubble }): JSX.Element {
  if (bubble.kind === 'text') {
    return <div className={`bubble ${bubble.role}`}>{bubble.content || '…'}</div>;
  }
  if (bubble.kind === 'tool') {
    let resultRender: string | undefined;
    if (bubble.resultText) {
      try {
        resultRender = JSON.stringify(JSON.parse(bubble.resultText), null, 2);
      } catch {
        resultRender = bubble.resultText;
      }
    }
    let argsRender: string | undefined;
    if (bubble.argsJson) {
      try {
        argsRender = JSON.stringify(JSON.parse(bubble.argsJson), null, 2);
      } catch {
        argsRender = bubble.argsJson;
      }
    }
    return (
      <div className="bubble tool">
        <div className="tool-label">🔧 {bubble.toolName}</div>
        {argsRender && <pre>args: {argsRender}</pre>}
        {resultRender && <pre>result: {resultRender}</pre>}
        {!resultRender && argsRender && <pre style={{ opacity: 0.6 }}>(awaiting result…)</pre>}
      </div>
    );
  }
  return <div className="bubble error">Error: {bubble.message}</div>;
}
