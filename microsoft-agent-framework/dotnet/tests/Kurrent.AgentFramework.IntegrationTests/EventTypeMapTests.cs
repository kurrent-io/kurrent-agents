using Kurrent.Agent.Schema;
using Kurrent.Agent.Schema.Events;

namespace Kurrent.AgentFramework.IntegrationTests;

public class EventTypeMapTests {
    /// <summary>
    /// The MAF .NET integration consumes a subset of the canonical vocabulary.
    /// As of DEV-1611, interrupts (kind=approval) are emitted for tool-approval
    /// flows. Subagents, thinking, and artifacts are still not produced by the
    /// MAF side. This list locks in the subset we exercise; the shared
    /// <see cref="EventTypeMap.All"/> is the authoritative full registry covered
    /// by <c>FixtureRoundTripTests</c> in the schema package.
    /// </summary>
    public static IEnumerable<(Type ClrType, string Name)> KnownEventTypes() => [
        (typeof(SessionStarted), "SessionStarted"),
        (typeof(SessionEnded), "SessionEnded"),
        (typeof(UserMessageReceived), "UserMessageReceived"),
        (typeof(AssistantTextGenerated), "AssistantTextGenerated"),
        (typeof(AssistantToolCallsGenerated), "AssistantToolCallsGenerated"),
        (typeof(ToolResultReceived), "ToolResultReceived"),
        (typeof(InterruptIssued), "InterruptIssued"),
        (typeof(InterruptResolved), "InterruptResolved"),
        (typeof(FactRetained), "FactRetained"),
        (typeof(EvalRunStarted), "EvalRunStarted"),
        (typeof(TurnScored), "TurnScored"),
        (typeof(EvalRunCompleted), "EvalRunCompleted"),
    ];

    [Test]
    [MethodDataSource(nameof(KnownEventTypes))]
    public async Task NameMapsToClrType(Type clrType, string expectedName) {
        await Assert.That(EventTypeMap.All[expectedName]).IsEqualTo(clrType);
    }

    [Test]
    [MethodDataSource(nameof(KnownEventTypes))]
    public async Task ClrTypeMapsBackToName(Type expectedClrType, string name) {
        var byType = EventTypeMap.All.First(kv => kv.Value == expectedClrType).Key;
        await Assert.That(byType).IsEqualTo(name);
    }

    [Test]
    public async Task UnknownNameReturnsFalse() {
        await Assert.That(EventTypeMap.All.ContainsKey("SomeUnknownEvent")).IsFalse();
    }
}
