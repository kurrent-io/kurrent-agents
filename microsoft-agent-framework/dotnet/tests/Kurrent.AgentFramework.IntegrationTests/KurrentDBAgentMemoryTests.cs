using Kurrent.AgentFramework;
using Kurrent.AgentFramework.Memory;
using KurrentDB.Client;
using Microsoft.Extensions.DependencyInjection;

namespace Kurrent.AgentFramework.IntegrationTests;

[ClassDataSource<KurrentDbFixture>(Shared = SharedType.PerTestSession)]
public class KurrentDBAgentMemoryTests(KurrentDbFixture db) {
    // Fresh identifiers per test so canonical streams (AgentMemory-{app}-{user})
    // don't collide across the shared-fixture test session.
    static (string AppName, string UserId) NewIds() =>
        ($"app-{Guid.NewGuid():N}", $"user-{Guid.NewGuid():N}");

    static async Task<List<string>> Collect(IAsyncEnumerable<string> source) {
        var list = new List<string>();
        await foreach (var item in source) list.Add(item);
        return list;
    }

    [Test]
    public async Task Retain_ThenRecall_ReturnsFactsNewestFirst() {
        using var client       = db.CreateClient();
        var (appName, userId)  = NewIds();
        var memory             = new KurrentDBAgentMemory(client, appName, userId);

        await memory.RetainAsync("first");
        await memory.RetainAsync("second");
        await memory.RetainAsync("third");

        var recalled = await Collect(memory.RecallAsync("ignored"));

        await Assert.That(recalled).IsEquivalentTo(new[] { "third", "second", "first" });
    }

    [Test]
    public async Task Recall_OnMissingStream_ReturnsEmpty() {
        using var client       = db.CreateClient();
        var (appName, userId)  = NewIds();
        var memory             = new KurrentDBAgentMemory(client, appName, userId);

        var recalled = await Collect(memory.RecallAsync("ignored"));

        await Assert.That(recalled).IsEmpty();
    }

    [Test]
    public async Task Retain_WhitespaceFact_IsIgnored() {
        using var client       = db.CreateClient();
        var (appName, userId)  = NewIds();
        var memory             = new KurrentDBAgentMemory(client, appName, userId);

        await memory.RetainAsync("   ");
        await memory.RetainAsync("");
        await memory.RetainAsync("real");

        var recalled = await Collect(memory.RecallAsync(""));

        await Assert.That(recalled).IsEquivalentTo(new[] { "real" });
    }

    [Test]
    public async Task Recall_IgnoresQueryText() {
        using var client       = db.CreateClient();
        var (appName, userId)  = NewIds();
        var memory             = new KurrentDBAgentMemory(client, appName, userId);

        await memory.RetainAsync("alpha");
        await memory.RetainAsync("beta");

        var q1 = await Collect(memory.RecallAsync("alpha"));
        var q2 = await Collect(memory.RecallAsync("banana"));

        // Current behavior: query is ignored, consumers filter themselves.
        await Assert.That(q1).IsEquivalentTo(q2);
    }

    [Test]
    public async Task DefaultStream_FollowsCanonicalConvention() {
        // SCHEMA_v2 §2.1: facts land in AgentMemory-{app}-{user}. Two memory
        // instances with different userIds must not see each other's facts.
        using var client = db.CreateClient();
        var appName      = $"app-{Guid.NewGuid():N}";
        var userA        = $"user-{Guid.NewGuid():N}";
        var userB        = $"user-{Guid.NewGuid():N}";

        var memoryA = new KurrentDBAgentMemory(client, appName, userA);
        var memoryB = new KurrentDBAgentMemory(client, appName, userB);

        await memoryA.RetainAsync("only-for-a");
        await memoryB.RetainAsync("only-for-b");

        var factsA = await Collect(memoryA.RecallAsync(""));
        var factsB = await Collect(memoryB.RecallAsync(""));

        await Assert.That(factsA).IsEquivalentTo(new[] { "only-for-a" });
        await Assert.That(factsB).IsEquivalentTo(new[] { "only-for-b" });
    }

    [Test]
    [Arguments("", "user1")]
    [Arguments("   ", "user1")]
    [Arguments("app1", "")]
    [Arguments("app1", "\t")]
    public async Task EmptyIdentifiers_Rejected(string appName, string userId) {
        // Blank identifiers would collapse tenant isolation into AgentMemory--
        // style streams — reject at the boundary rather than silently sharing.
        using var client = db.CreateClient();

        await Assert.That(() => new KurrentDBAgentMemory(client, appName, userId))
            .Throws<ArgumentException>();
    }

    [Test]
    public async Task MissingIdentifiers_Rejected_WithoutStreamNameOverride() {
        // Without a streamName override, both ids must be supplied — otherwise we'd
        // build a degenerate canonical stream.
        using var client = db.CreateClient();

        await Assert.That(() => new KurrentDBAgentMemory(client))
            .Throws<ArgumentException>();
    }

    [Test]
    [Arguments("")]
    [Arguments("   ")]
    [Arguments("\t")]
    public async Task StreamName_BlankOverride_Rejected(string blankStreamName) {
        // Fail fast at the boundary rather than pushing a blank stream name through
        // to KurrentDB where it surfaces as an opaque error on first append.
        using var client = db.CreateClient();

        await Assert.That(() => new KurrentDBAgentMemory(client, streamName: blankStreamName))
            .Throws<ArgumentException>();
    }

    [Test]
    public async Task StreamName_Override_BypassesIdentifierValidation() {
        // The override is the escape hatch for deliberately shared / cross-tenant
        // memory; appName/userId are irrelevant once it's set.
        using var client = db.CreateClient();
        var streamName   = $"AgentMemory-override-{Guid.NewGuid():N}";

        var memory = new KurrentDBAgentMemory(client, streamName: streamName);
        await memory.RetainAsync("scoped fact");

        var recalled = await Collect(memory.RecallAsync(""));
        await Assert.That(recalled).IsEquivalentTo(new[] { "scoped fact" });
    }

    [Test]
    public async Task AddKurrentAgentMemory_StreamNameOnly_RegistersSharedMemoryWithoutIds() {
        // Regression for the DI-vs-docs mismatch: the extension must allow registering
        // a shared / cross-tenant memory instance using only streamName, matching the
        // constructor's escape-hatch semantics.
        using var client = db.CreateClient();
        var streamName   = $"AgentMemory-shared-{Guid.NewGuid():N}";

        var services = new ServiceCollection();
        services.AddSingleton(client);
        services.AddKurrentAgentMemory(streamName: streamName);

        await using var sp = services.BuildServiceProvider();
        var memory         = sp.GetRequiredService<IAgentMemory>();

        await memory.RetainAsync("shared fact");
        var recalled = await Collect(memory.RecallAsync(""));

        await Assert.That(recalled).IsEquivalentTo(new[] { "shared fact" });
    }

    [Test]
    public async Task StreamName_Override_IsolatesFromCanonicalDefault() {
        using var client      = db.CreateClient();
        var (appName, userId) = NewIds();
        var overrideStream    = $"AgentMemory-override-{Guid.NewGuid():N}";

        var canonical = new KurrentDBAgentMemory(client, appName, userId);
        var overridden = new KurrentDBAgentMemory(client, appName, userId, streamName: overrideStream);

        await canonical.RetainAsync("in-canonical");
        await overridden.RetainAsync("in-override");

        await Assert.That(await Collect(canonical.RecallAsync(""))).IsEquivalentTo(new[] { "in-canonical" });
        await Assert.That(await Collect(overridden.RecallAsync(""))).IsEquivalentTo(new[] { "in-override" });
    }
}
