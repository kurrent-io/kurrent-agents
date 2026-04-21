using System.Text.Json;

namespace Kurrent.Agent.Schema.Events;

/// <summary>First event of an agent session stream. See SCHEMA_v2 §3.1.</summary>
public sealed record SessionStarted(
    string?                                    AppName,
    string?                                    AgentName,
    string?                                    Model,
    string?                                    TenantId,
    string?                                    UserId,
    AgentConfig?                               AgentConfig,
    string?                                    PreviousSessionId,
    DateTimeOffset                             Timestamp,
    IReadOnlyDictionary<string, JsonElement>?  Extensions = null
);

/// <summary>Marks logical end of a session. Stream is not truncated.</summary>
public sealed record SessionEnded(
    string?                                    Reason,
    DateTimeOffset                             Timestamp,
    IReadOnlyDictionary<string, JsonElement>?  Extensions = null
);

/// <summary>
/// Written to the predecessor session pointing forward to its successor.
/// Paired with <see cref="SessionStarted.PreviousSessionId"/>. New in v2.
/// </summary>
public sealed record SessionContinuedAs(
    string                                     NextSessionId,
    string?                                    Reason,
    DateTimeOffset                             Timestamp,
    IReadOnlyDictionary<string, JsonElement>?  Extensions = null
);
