using Google.Protobuf;
using Kurrent.Agent.Schema.Events;

namespace Kurrent.Agent.Schema;

/// <summary>KurrentDB event_type ↔ generated message type registry.</summary>
public static class EventTypeMap {
    public static IReadOnlyDictionary<string, Type> All { get; } = new Dictionary<string, Type> {
        ["SessionStarted"]                = typeof(SessionStarted),
        ["SessionEnded"]                  = typeof(SessionEnded),
        ["SessionContinuedAs"]            = typeof(SessionContinuedAs),
        ["UserMessageReceived"]           = typeof(UserMessageReceived),
        ["AssistantTextGenerated"]        = typeof(AssistantTextGenerated),
        ["AssistantToolCallsGenerated"]   = typeof(AssistantToolCallsGenerated),
        ["AssistantThinkingGenerated"]    = typeof(AssistantThinkingGenerated),
        ["ToolResultReceived"]            = typeof(ToolResultReceived),
        ["InterruptIssued"]               = typeof(InterruptIssued),
        ["InterruptResolved"]             = typeof(InterruptResolved),
        ["SubagentStarted"]               = typeof(SubagentStarted),
        ["SubagentCompleted"]             = typeof(SubagentCompleted),
        ["FactRetained"]                  = typeof(FactRetained),
        ["ArtifactVersionCreated"]        = typeof(ArtifactVersionCreated),
        ["EvalRunStarted"]                = typeof(EvalRunStarted),
        ["TurnScored"]                    = typeof(TurnScored),
        ["EvalRunCompleted"]              = typeof(EvalRunCompleted),
    };
}
