namespace Kurrent.AgentFramework.IntegrationTests;

public class StreamNameTests {
    [Test]
    public async Task ForSession_UsesAgentSessionCategoryPrefix() {
        var streamName = StreamName.ForSession("abc-123");

        await Assert.That(streamName).IsEqualTo("AgentSession-abc-123");
    }

    [Test]
    public async Task ForSession_AcceptsGuidSessionIds() {
        var sessionId  = Guid.NewGuid().ToString("N");
        var streamName = StreamName.ForSession(sessionId);

        await Assert.That(streamName).StartsWith("AgentSession-");
        await Assert.That(streamName).EndsWith(sessionId);
    }

    [Test]
    public async Task ForSession_PreservesEmptyId() {
        // No invariant forbids it today; the test locks in current behavior so
        // any future validation change is a deliberate decision, not an accident.
        var streamName = StreamName.ForSession("");

        await Assert.That(streamName).IsEqualTo("AgentSession-");
    }
}
