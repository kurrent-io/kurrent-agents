# handoff_demo

Demonstrates canonical handoff promotion via `KurrentDBSession` (AI-471).

A triage agent routes the user to one of two specialists. The session writes:

- `AgentSession-{id}` (parent): triage's turns + `SubagentStarted` / `SubagentCompleted` lifecycle.
- `AgentSubsession-{id}-{agent_id}` (subagent): the specialist's transcript.

Run:

    docker compose up -d                  # KurrentDB
    export OPENAI_API_KEY=sk-...
    python -m samples.handoff_demo.main   # from openai-agents/python/

The `session=s, hooks=s` wiring is required for canonical handoff
promotion. Without `hooks=s`, the handoff tool call is still persisted
as a regular canonical tool call (`AssistantToolCallsGenerated` /
`ToolResultReceived`) on the parent stream, but the `SubagentStarted` /
`SubagentCompleted` lifecycle and the subsession transcript stream are
not emitted — cross-framework readers (ADK / MAF / Strands / Capacitor)
won't see the subagent context.
