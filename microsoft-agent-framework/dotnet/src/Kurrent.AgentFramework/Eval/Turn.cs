namespace Kurrent.AgentFramework.Eval;

/// <summary>
/// A single conversation turn extracted from an agent session stream.
/// </summary>
public sealed record Turn(
        int                     Index,
        string?                 UserInput,
        string?                 AssistantOutput,
        IReadOnlyList<ToolCall> ToolCalls,
        long?                   InputTokens,
        long?                   OutputTokens
    );

public sealed record ToolCall(
        string  Name,
        string? Arguments,
        string? Result,
        bool    IsError
    );
