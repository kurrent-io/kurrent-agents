# ag-ui-showcase — kurrent-agents customer demo

A 4-lane chat UI that shows the canonical KurrentDB schema as the unifying
contract across **multiple framework backends**. Same browser UI talks to:

- **MAF Python** — native Kurrent integration writes canonical events;
  browser sees a live replay via DEV-1559.
- **ADK Python** — same shape (Phase 2).
- **Strands Python** — same shape (Phase 2).
- **AG-UI lane** (LangGraph or Mastra) — wrapped with the DEV-1558
  middleware; real token-by-token streaming via AG-UI directly to the
  browser, persistence as a side-effect (Phase 2).

All 4 lanes write the same canonical schema to KurrentDB. Open
`http://localhost:2113` (KurrentDB admin) during the demo to see the
sessions side-by-side.

## Phasing

| Phase | Status | Scope |
|---|---|---|
| 1 | done | Vertical slice — MAF lane only, end-to-end. Bones working. |
| 2 | done | ADK + Strands lanes. Framework dropdown. (LangGraph deferred — was the only lane that exercised the DEV-1558 middleware; without it the demo only shows DEV-1559 read side.) |
| 2.5 | TODO | Real LLM mode (`ANTHROPIC_API_KEY` set). MAF/ADK/Strands native runners actually invoke their integrations instead of writing canned events. |
| 3 | TODO | Visual polish, session browser, error handling, one-command bring-up. |

## Architecture (Phase 2)

```
Browser (Vite + React, @ag-ui/client HttpAgent)
       │  framework dropdown picks the lane
       ▼ POST /agent/{maf|adk|strands}  (AG-UI RunAgentInput)
       ▼ SSE response                   (stream of AG-UI BaseEvent JSON)
TS Fastify server (server/)
       │
       ├── 1. Spawn Python subprocess: runners/{lane}/runner.py
       │      Each lane has its own venv (incompatible deps; see
       │      CLAUDE.md re: otel pin conflict between MAF and ADK).
       │      Runner writes canonical events to AgentSession-{id}.
       │
       └── 2. KurrentDBReplayAgent in `live` mode subscribes to
              AgentSession-{id}. As canonical events land, the replay
              agent emits AG-UI events. Server pipes them to browser.
                  ▲
                  │
              KurrentDB (single source of truth for the conversation)
```

Switching lanes mid-conversation is fine — every lane writes to the
same `AgentSession-{threadId}` stream. The canonical schema is the
contract; integrations don't need to know each other exists.

## Running (Phase 2 — dummy mode)

Prereqs:
- Node 20+, npm
- Python 3.11+, [uv](https://docs.astral.sh/uv/)
- Docker (for KurrentDB)

```bash
# 1. KurrentDB
cd ../demo
docker compose up -d

# 2. Python runner venvs — one per lane (otel pin incompatibility)
cd ag-ui-showcase
uv sync --project runners/maf
uv sync --project runners/adk
uv sync --project runners/strands

# 3. Install + run server
cd server
npm install
DUMMY_MODE=1 npm start         # in dummy mode — see below

# 4. Install + run UI (separate terminal)
cd ../ui
npm install
npm run dev                    # Vite dev server at http://localhost:5173

# 5. Open http://localhost:5173. Pick a framework. Type a message.
```

Phase 2.5 (real LLM): unset `DUMMY_MODE`, set `ANTHROPIC_API_KEY`,
restart the server. (Real-mode runners are stubbed today —
`NotImplementedError` until 2.5 ships.)

(A one-command bring-up script lands in Phase 3.)

## Why this exists

PR #57 ships DEV-1558 (write side) and DEV-1559 (read side). This demo
*uses* both in production-ish ways, against real framework integrations,
proving the canonical schema as a true cross-framework contract.

For the strategic positioning, see `ag-ui/GAPS.md`.
