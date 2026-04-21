using System.Text.Json;

namespace Kurrent.Agent.Schema.Events;

/// <summary>
/// Subagent lifecycle start, written to the parent session stream.
/// Subagent conversation lives in <c>AgentSubsession-{parent}-{agent_id}</c>.
/// See SCHEMA_v2 §3.5.
/// </summary>
public sealed record SubagentStarted(
    string                                     AgentId,
    string?                                    AgentType,
    string?                                    Prompt,
    string?                                    SubsessionStream,
    DateTimeOffset                             Timestamp,
    IReadOnlyDictionary<string, JsonElement>?  Extensions = null
);

/// <summary>Subagent lifecycle completion, written to the parent session stream.</summary>
public sealed record SubagentCompleted(
    string                                     AgentId,
    string?                                    Outcome,
    string?                                    Summary,
    DateTimeOffset                             Timestamp,
    IReadOnlyDictionary<string, JsonElement>?  Extensions = null
);
