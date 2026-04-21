using Kurrent.Agent.Schema.Events;

namespace Kurrent.Agent.Schema;

/// <summary>
/// Maps canonical CLR event types to/from KurrentDB event-type names.
/// Integration writers use <see cref="GetName"/> to stamp events;
/// readers use <see cref="GetType"/> for the inverse lookup.
/// </summary>
public static class EventTypeMap {
    static readonly IReadOnlyDictionary<Type, string> ClrToName = new Dictionary<Type, string> {
        [typeof(SessionStarted)]              = "SessionStarted",
        [typeof(SessionEnded)]                = "SessionEnded",
        [typeof(SessionContinuedAs)]          = "SessionContinuedAs",
        [typeof(UserMessageReceived)]         = "UserMessageReceived",
        [typeof(AssistantTextGenerated)]      = "AssistantTextGenerated",
        [typeof(AssistantToolCallsGenerated)] = "AssistantToolCallsGenerated",
        [typeof(AssistantThinkingGenerated)]  = "AssistantThinkingGenerated",
        [typeof(ToolResultReceived)]          = "ToolResultReceived",
        [typeof(InterruptIssued)]             = "InterruptIssued",
        [typeof(InterruptResolved)]           = "InterruptResolved",
        [typeof(SubagentStarted)]             = "SubagentStarted",
        [typeof(SubagentCompleted)]           = "SubagentCompleted",
        [typeof(FactRetained)]                = "FactRetained",
        [typeof(ArtifactVersionCreated)]      = "ArtifactVersionCreated",
        [typeof(EvalRunStarted)]              = "EvalRunStarted",
        [typeof(TurnScored)]                  = "TurnScored",
        [typeof(EvalRunCompleted)]            = "EvalRunCompleted",
    };

    static readonly IReadOnlyDictionary<string, Type> NameToClr =
        ClrToName.ToDictionary(x => x.Value, x => x.Key);

    public static string GetName(Type clrType) =>
        ClrToName.TryGetValue(clrType, out var name)
            ? name
            : throw new ArgumentException($"Unknown canonical event type: {clrType.Name}");

    public static Type? GetType(string eventTypeName) =>
        NameToClr.GetValueOrDefault(eventTypeName);

    public static IEnumerable<KeyValuePair<Type, string>> All => ClrToName;
}
