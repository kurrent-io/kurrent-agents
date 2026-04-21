using System.Text.Json;

namespace Kurrent.Agent.Schema.Events;

/// <summary>Tool description captured in <see cref="AgentConfig.Tools"/>.</summary>
public sealed record ToolSpec(
    string       Name,
    string?      Description,
    JsonElement? InputSchema,
    string?      Source
);

/// <summary>
/// Informational snapshot of the agent configuration at session start.
/// All fields optional; not a contract to reproduce. See SCHEMA_v2 §3.1.
/// </summary>
public sealed record AgentConfig(
    IReadOnlyList<ToolSpec>? Tools,
    IReadOnlyList<string>?   Plugins,
    JsonElement?             ConversationManager,
    JsonElement?             ModelParameters
);

/// <summary>One tool call within <see cref="ConversationEvents.AssistantToolCallsGenerated"/>.</summary>
public sealed record ToolCallInfo(
    string       CallId,
    string       ToolName,
    JsonElement? Arguments
);
