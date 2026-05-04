# kurrent-ag-ui

Read-side bridge from canonical KurrentDB agent sessions to the
[AG-UI Protocol](https://github.com/ag-ui-protocol/ag-ui).

Subscribes to an `AgentSession-{id}` stream, translates canonical events
(`UserMessageReceived`, `AssistantTextGenerated`, `AssistantToolCallsGenerated`,
`ToolResultReceived`, …) into AG-UI events, and serves them as SSE.

Because every framework integration in this monorepo (ADK, MAF, Strands,
OpenAI Agents, Claude SDK) writes the same canonical schema, one bridge gives
session-replay UIs for all of them.

## Scope

- **Read-only.** Translates KurrentDB → AG-UI for replay / audit / live-tail.
- **Message-grained, not token-grained.** Canonical events are post-hoc
  message records, so each text/tool-call becomes
  `*_START → one *_CONTENT/_ARGS → *_END`. Wire-legal AG-UI; chunky replay.
- **No write-side.** AG-UI → canonical isn't implemented; the impedance
  mismatch (token deltas vs message records) makes it future work.

## Layout

- `kurrent_ag_ui/translator.py` — pure function, canonical → list[AG-UI dict].
- `kurrent_ag_ui/bridge.py` — async stream reader, KurrentDB → AG-UI events.
- `kurrent_ag_ui/server.py` — SSE HTTP wrapper (Starlette).

## Mapping

| Canonical | AG-UI |
|---|---|
| `SessionStarted` | `RUN_STARTED` |
| `UserMessageReceived` | `TEXT_MESSAGE_START`(role=user) → `_CONTENT` → `_END` |
| `AssistantTextGenerated` | `TEXT_MESSAGE_START`(role=assistant) → `_CONTENT` → `_END` |
| `AssistantThinkingGenerated` | `REASONING_MESSAGE_START` → `_CONTENT` → `_END` |
| `AssistantToolCallsGenerated` | optional preceding text; per call: `TOOL_CALL_START` → `_ARGS` → `_END` |
| `ToolResultReceived` | `TOOL_CALL_RESULT` |
| `InterruptIssued` / `InterruptResolved` | `CUSTOM` (`interrupt.issued` / `.resolved`) |
| `SubagentStarted` / `SubagentCompleted` | `CUSTOM` (`subagent.started` / `.completed`) |
| `SessionEnded` | `RUN_FINISHED` |
| Other (`FactRetained`, `EvalRunStarted`, …) | `CUSTOM` pass-through |
