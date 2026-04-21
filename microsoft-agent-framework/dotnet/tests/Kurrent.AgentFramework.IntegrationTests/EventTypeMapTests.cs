using Kurrent.AgentFramework.Events;
using Kurrent.AgentFramework.Serialization;

namespace Kurrent.AgentFramework.IntegrationTests;

public class EventTypeMapTests {
    public static IEnumerable<(Type ClrType, string Name)> KnownEventTypes() => [
        (typeof(SessionStarted),              "SessionStarted"),
        (typeof(SessionEnded),                "SessionEnded"),
        (typeof(UserMessageReceived),         "UserMessageReceived"),
        (typeof(AssistantTextGenerated),      "AssistantTextGenerated"),
        (typeof(AssistantToolCallsGenerated), "AssistantToolCallsGenerated"),
        (typeof(ToolResultReceived),          "ToolResultReceived"),
        (typeof(FactRetained),                "FactRetained"),
        (typeof(TokenUsageRecorded),          "TokenUsageRecorded"),
        (typeof(EvalRunStarted),              "EvalRunStarted"),
        (typeof(TurnScored),                  "TurnScored"),
        (typeof(EvalRunCompleted),            "EvalRunCompleted"),
    ];

    [Test]
    [MethodDataSource(nameof(KnownEventTypes))]
    public async Task GetEventTypeName_ReturnsExpectedName(Type clrType, string expectedName) {
        await Assert.That(EventTypeMap.GetEventTypeName(clrType)).IsEqualTo(expectedName);
    }

    [Test]
    [MethodDataSource(nameof(KnownEventTypes))]
    public async Task GetClrType_ResolvesBackToClrType(Type expectedClrType, string name) {
        await Assert.That(EventTypeMap.GetClrType(name)).IsEqualTo(expectedClrType);
    }

    [Test]
    public async Task GetEventTypeName_ThrowsForUnknownClrType() {
        await Assert.That(() => EventTypeMap.GetEventTypeName(typeof(string)))
            .ThrowsExactly<ArgumentException>();
    }

    [Test]
    public async Task GetClrType_ReturnsNullForUnknownName() {
        await Assert.That(EventTypeMap.GetClrType("SomeUnknownEvent")).IsNull();
    }
}
