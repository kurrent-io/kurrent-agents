using System.Text.Json;

namespace Kurrent.Agent.Schema.Events;

/// <summary>
/// Mid-turn human-in-the-loop pause (permission / approval / input / auth).
/// <see cref="Kind"/> is an open string; readers tolerate unknown values.
/// Framework-specific details live in <c>extensions.{slug}.interrupt</c>.
/// <see cref="MessageId"/> anchors the interrupt to its carrier message in
/// frameworks that bundle approval requests inside chat messages (e.g. MAF
/// <c>FunctionApprovalRequestContent</c> on an assistant message); null for
/// standalone interrupts (e.g. Claude Code permission prompts).
/// New in v2. See SCHEMA_v2 §3.3 for the post-hoc / pre-hoc gating split
/// that governs <see cref="RequestId"/>.
/// </summary>
public sealed record InterruptIssued(
    string                                     RequestId,
    string                                     Kind,
    string?                                    ToolName,
    string?                                    Prompt,
    string?                                    MessageId,
    DateTimeOffset                             Timestamp,
    IReadOnlyDictionary<string, JsonElement>?  Extensions = null
);

/// <summary>
/// Resolution of an <see cref="InterruptIssued"/>, keyed by matching
/// <see cref="RequestId"/>. <see cref="Outcome"/> documented set:
/// <c>allow | allow_once | allow_always | deny | cancel | answered | timeout</c>.
/// <see cref="MessageId"/> anchors the resolution to its carrier message
/// when one exists (e.g. MAF <c>FunctionApprovalResponseContent</c> on a
/// user message). New in v2.
/// </summary>
public sealed record InterruptResolved(
    string                                     RequestId,
    string                                     Outcome,
    string?                                    Response,
    string?                                    MessageId,
    DateTimeOffset                             Timestamp,
    IReadOnlyDictionary<string, JsonElement>?  Extensions = null
);
