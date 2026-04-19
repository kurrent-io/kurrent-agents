using Kurrent.AgentFramework.Memory;

namespace Kurrent.AgentFramework.IntegrationTests;

[ClassDataSource<KurrentDbFixture>(Shared = SharedType.PerTestSession)]
public class KurrentDBAgentMemoryTests(KurrentDbFixture db) {
    static string NewStreamName() => $"AgentMemory-{Guid.NewGuid():N}";

    static async Task<List<string>> Collect(IAsyncEnumerable<string> source) {
        var list = new List<string>();
        await foreach (var item in source) list.Add(item);
        return list;
    }

    [Test]
    public async Task Retain_ThenRecall_ReturnsFactsNewestFirst() {
        using var client = db.CreateClient();
        var memory       = new KurrentDBAgentMemory(client, NewStreamName());

        await memory.RetainAsync("first");
        await memory.RetainAsync("second");
        await memory.RetainAsync("third");

        var recalled = await Collect(memory.RecallAsync("ignored"));

        await Assert.That(recalled).IsEquivalentTo(new[] { "third", "second", "first" });
    }

    [Test]
    public async Task Recall_OnMissingStream_ReturnsEmpty() {
        using var client = db.CreateClient();
        var memory       = new KurrentDBAgentMemory(client, NewStreamName());

        var recalled = await Collect(memory.RecallAsync("ignored"));

        await Assert.That(recalled).IsEmpty();
    }

    [Test]
    public async Task Retain_WhitespaceFact_IsIgnored() {
        using var client = db.CreateClient();
        var memory       = new KurrentDBAgentMemory(client, NewStreamName());

        await memory.RetainAsync("   ");
        await memory.RetainAsync("");
        await memory.RetainAsync("real");

        var recalled = await Collect(memory.RecallAsync(""));

        await Assert.That(recalled).IsEquivalentTo(new[] { "real" });
    }

    [Test]
    public async Task Recall_IgnoresQueryText() {
        using var client = db.CreateClient();
        var memory       = new KurrentDBAgentMemory(client, NewStreamName());

        await memory.RetainAsync("alpha");
        await memory.RetainAsync("beta");

        var q1 = await Collect(memory.RecallAsync("alpha"));
        var q2 = await Collect(memory.RecallAsync("banana"));

        // Current behavior: query is ignored, consumers filter themselves.
        await Assert.That(q1).IsEquivalentTo(q2);
    }

    [Test]
    public async Task Retain_RespectsCustomStreamName() {
        using var client = db.CreateClient();
        var streamA      = NewStreamName();
        var streamB      = NewStreamName();
        var memoryA      = new KurrentDBAgentMemory(client, streamA);
        var memoryB      = new KurrentDBAgentMemory(client, streamB);

        await memoryA.RetainAsync("only-in-a");
        await memoryB.RetainAsync("only-in-b");

        var factsA = await Collect(memoryA.RecallAsync(""));
        var factsB = await Collect(memoryB.RecallAsync(""));

        await Assert.That(factsA).IsEquivalentTo(new[] { "only-in-a" });
        await Assert.That(factsB).IsEquivalentTo(new[] { "only-in-b" });
    }
}
