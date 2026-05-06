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
| 1 | in progress | Vertical slice — MAF lane only, end-to-end. Bones working. |
| 2 | TODO | Add ADK + Strands + LangGraph lanes. Framework dropdown. |
| 3 | TODO | Visual polish, session browser, error handling, one-command bring-up. |

## Architecture (Phase 1)

```
Browser (Vite + React, @ag-ui/client HttpAgent)
       │
       ▼ POST /agent/maf  (AG-UI RunAgentInput)
       ▼ SSE response     (stream of AG-UI BaseEvent JSON)
TS Fastify server (server/)
       │
       ├── 1. Spawn Python subprocess: runners/maf_runner.py
       │    (writes canonical events to KurrentDB AgentSession-{id})
       │
       └── 2. KurrentDBReplayAgent in `live` mode subscribes to
              AgentSession-{id}. As canonical events land, replay
              agent emits AG-UI events. Server pipes them to browser.
                  ▲
                  │
              KurrentDB (single source of truth for the conversation)
```

## Running (Phase 1)

Prereqs:
- Node 20+, npm
- Python 3.11+, [uv](https://docs.astral.sh/uv/)
- Docker (for KurrentDB)
- `ANTHROPIC_API_KEY` env var — **OR** `DUMMY_MODE=1` to use canned responses

```bash
# 1. KurrentDB
cd ../        # demo/ root
docker compose up -d

# 2. Python runner deps (one-time)
cd ag-ui-showcase
uv sync

# 3. Install + build server
cd server
npm install
npm run build

# 4. Install + run UI
cd ../ui
npm install
npm run dev   # Vite dev server at http://localhost:5173

# 5. Run server (separate terminal)
cd ../server
DUMMY_MODE=1 npm start    # or set ANTHROPIC_API_KEY for real LLM

# 6. Open http://localhost:5173 in your browser.
```

(A one-command bring-up script lands in Phase 3.)

## Why this exists

PR #57 ships DEV-1558 (write side) and DEV-1559 (read side). This demo
*uses* both in production-ish ways, against real framework integrations,
proving the canonical schema as a true cross-framework contract.

For the strategic positioning, see `ag-ui/GAPS.md`.
