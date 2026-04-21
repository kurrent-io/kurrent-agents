using Kurrent.AgentFramework.Events;

namespace Kurrent.AgentFramework.Serialization;

/// <summary>
/// Maps CLR event types to/from KurrentDB event type names.
/// </summary>
public static class EventTypeMap {
    static readonly Dictionary<Type, string> ClrToName = new() {
        [typeof(SessionStarted)]              = "SessionStarted",
        [typeof(SessionEnded)]                = "SessionEnded",
        [typeof(UserMessageReceived)]         = "UserMessageReceived",
        [typeof(AssistantTextGenerated)]      = "AssistantTextGenerated",
        [typeof(AssistantToolCallsGenerated)] = "AssistantToolCallsGenerated",
        [typeof(ToolResultReceived)]          = "ToolResultReceived",
        [typeof(FactRetained)]                = "FactRetained",
        [typeof(TokenUsageRecorded)]          = "TokenUsageRecorded",
        [typeof(EvalRunStarted)]              = "EvalRunStarted",
        [typeof(TurnScored)]                  = "TurnScored",
        [typeof(EvalRunCompleted)]            = "EvalRunCompleted",
    };

    static readonly Dictionary<string, Type> NameToClr =
        ClrToName.ToDictionary(x => x.Value, x => x.Key);

    public static string GetEventTypeName(Type clrType) =>
        ClrToName.TryGetValue(clrType, out var name)
            ? name
            : throw new ArgumentException($"Unknown event type: {clrType.Name}");

    public static Type? GetClrType(string eventTypeName) => NameToClr.GetValueOrDefault(eventTypeName);
}
