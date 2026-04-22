using Kurrent.Agent.Schema;
using Kurrent.Agent.Schema.Events;

namespace Kurrent.AgentFramework.IntegrationTests;

public class EventTypeMapTests {
    /// <summary>
    /// The MAF .NET integration only consumes a subset of the canonical vocabulary today
    /// (no interrupts, subagents, thinking, or artifacts from the MAF side yet). This list
    /// locks in the subset we do exercise; the shared <see cref="EventTypeMap.All"/> is the
    /// authoritative full registry covered by <c>FixtureRoundTripTests</c> in the schema package.
    /// </summary>
    public static IEnumerable<(Type ClrType, string Name)> KnownEventTypes() => [
        (typeof(SessionStarted),              "SessionStarted"),
        (typeof(SessionEnded),                "SessionEnded"),
        (typeof(UserMessageReceived),         "UserMessageReceived"),
        (typeof(AssistantTextGenerated),      "AssistantTextGenerated"),
        (typeof(AssistantToolCallsGenerated), "AssistantToolCallsGenerated"),
        (typeof(ToolResultReceived),          "ToolResultReceived"),
        (typeof(FactRetained),                "FactRetained"),
        (typeof(EvalRunStarted),              "EvalRunStarted"),
        (typeof(TurnScored),                  "TurnScored"),
        (typeof(EvalRunCompleted),            "EvalRunCompleted"),
    ];

    [Test]
    [MethodDataSource(nameof(KnownEventTypes))]
    public async Task GetName_ReturnsExpectedName(Type clrType, string expectedName) {
        await Assert.That(EventTypeMap.GetName(clrType)).IsEqualTo(expectedName);
    }

    [Test]
    [MethodDataSource(nameof(KnownEventTypes))]
    public async Task GetType_ResolvesBackToClrType(Type expectedClrType, string name) {
        await Assert.That(EventTypeMap.GetType(name)).IsEqualTo(expectedClrType);
    }

    [Test]
    public async Task GetName_ThrowsForUnknownClrType() {
        await Assert.That(() => EventTypeMap.GetName(typeof(string)))
            .ThrowsExactly<ArgumentException>();
    }

    [Test]
    public async Task GetType_ReturnsNullForUnknownName() {
        await Assert.That(EventTypeMap.GetType("SomeUnknownEvent")).IsNull();
    }
}
