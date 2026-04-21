using System.Text.Json;

namespace Kurrent.Agent.Schema.Events;

/// <summary>
/// Mid-turn human-in-the-loop pause (permission / approval / input / auth).
/// <see cref="Kind"/> is an open string; readers tolerate unknown values.
/// Framework-specific details live in <c>extensions.{slug}.interrupt</c>.
/// New in v2. See SCHEMA_v2 §3.3.
/// </summary>
public sealed record InterruptIssued(
    string                                     RequestId,
    string                                     Kind,
    string?                                    ToolName,
    string?                                    Prompt,
    DateTimeOffset                             Timestamp,
    IReadOnlyDictionary<string, JsonElement>?  Extensions = null
);

/// <summary>
/// Resolution of an <see cref="InterruptIssued"/>, keyed by matching
/// <see cref="RequestId"/>. <see cref="Outcome"/> documented set:
/// <c>allow | allow_once | allow_always | deny | cancel | answered | timeout</c>.
/// New in v2.
/// </summary>
public sealed record InterruptResolved(
    string                                     RequestId,
    string                                     Outcome,
    string?                                    Response,
    DateTimeOffset                             Timestamp,
    IReadOnlyDictionary<string, JsonElement>?  Extensions = null
);
