using System.Text.Json;
using Kurrent.AgentFramework.Workflows;
using Microsoft.Agents.AI.Workflows;

namespace Kurrent.AgentFramework.IntegrationTests;

/// <summary>
/// Exercises <see cref="KurrentDBCheckpointStore"/> against a real KurrentDB container.
/// Each test uses a fresh sessionId so checkpoint streams don't overlap.
/// </summary>
[ClassDataSource<KurrentDbFixture>(Shared = SharedType.PerTestSession)]
public class KurrentDBCheckpointStoreTests(KurrentDbFixture db) {
    static string NewSessionId() => Guid.NewGuid().ToString("N");

    static JsonElement Payload(object value) =>
        JsonSerializer.SerializeToElement(value);

    [Test]
    public async Task CreateCheckpoint_ReturnsInfoWithSessionAndCheckpointId() {
        using var client = db.CreateClient();
        var store        = new KurrentDBCheckpointStore(client);
        var sessionId    = NewSessionId();

        var info = await store.CreateCheckpointAsync(sessionId, Payload(new { step = 1 }));

        await Assert.That(info.SessionId).IsEqualTo(sessionId);
        await Assert.That(string.IsNullOrEmpty(info.CheckpointId)).IsFalse();
    }

    [Test]
    public async Task RetrieveCheckpoint_ReturnsOriginalPayload() {
        using var client = db.CreateClient();
        var store        = new KurrentDBCheckpointStore(client);
        var sessionId    = NewSessionId();

        var payload = Payload(new { step = 42, label = "midway" });
        var info    = await store.CreateCheckpointAsync(sessionId, payload);

        var retrieved = await store.RetrieveCheckpointAsync(sessionId, info);

        await Assert.That(retrieved.GetProperty("step").GetInt32()).IsEqualTo(42);
        await Assert.That(retrieved.GetProperty("label").GetString()).IsEqualTo("midway");
    }

    [Test]
    public async Task RetrieveCheckpoint_UnknownId_ThrowsKeyNotFound() {
        using var client = db.CreateClient();
        var store        = new KurrentDBCheckpointStore(client);
        var sessionId    = NewSessionId();

        // Stream must exist for the scan to reach the "not found among events" path.
        await store.CreateCheckpointAsync(sessionId, Payload(new { step = 1 }));

        var bogus = new CheckpointInfo(sessionId, "never-existed");

        await Assert.That(async () => await store.RetrieveCheckpointAsync(sessionId, bogus))
            .ThrowsExactly<KeyNotFoundException>();
    }

    [Test]
    public async Task RetrieveIndex_ReturnsAllCheckpointsForSession() {
        using var client = db.CreateClient();
        var store        = new KurrentDBCheckpointStore(client);
        var sessionId    = NewSessionId();

        var a = await store.CreateCheckpointAsync(sessionId, Payload(new { step = 1 }));
        var b = await store.CreateCheckpointAsync(sessionId, Payload(new { step = 2 }));
        var c = await store.CreateCheckpointAsync(sessionId, Payload(new { step = 3 }));

        var all = (await store.RetrieveIndexAsync(sessionId)).ToList();

        await Assert.That(all.Count).IsEqualTo(3);
        await Assert.That(all.Select(x => x.CheckpointId)).Contains(a.CheckpointId);
        await Assert.That(all.Select(x => x.CheckpointId)).Contains(b.CheckpointId);
        await Assert.That(all.Select(x => x.CheckpointId)).Contains(c.CheckpointId);
    }

    [Test]
    public async Task RetrieveIndex_WithParent_ReturnsOnlyChildrenOfThatParent() {
        using var client = db.CreateClient();
        var store        = new KurrentDBCheckpointStore(client);
        var sessionId    = NewSessionId();

        var root    = await store.CreateCheckpointAsync(sessionId, Payload(new { step = "root" }));
        var childA  = await store.CreateCheckpointAsync(sessionId, Payload(new { step = "a" }), parent: root);
        var childB  = await store.CreateCheckpointAsync(sessionId, Payload(new { step = "b" }), parent: root);
        var sibling = await store.CreateCheckpointAsync(sessionId, Payload(new { step = "sibling" }));

        var children = (await store.RetrieveIndexAsync(sessionId, withParent: root)).ToList();

        await Assert.That(children.Count).IsEqualTo(2);
        var ids = children.Select(x => x.CheckpointId).ToList();
        await Assert.That(ids).Contains(childA.CheckpointId);
        await Assert.That(ids).Contains(childB.CheckpointId);
        await Assert.That(ids).DoesNotContain(root.CheckpointId);
        await Assert.That(ids).DoesNotContain(sibling.CheckpointId);
    }

    [Test]
    public async Task RetrieveIndex_UnknownSession_ReturnsEmpty() {
        using var client = db.CreateClient();
        var store        = new KurrentDBCheckpointStore(client);

        var empty = await store.RetrieveIndexAsync(NewSessionId());

        await Assert.That(empty).IsEmpty();
    }

    [Test]
    public async Task CreateCheckpoint_SeparateSessions_AreIsolated() {
        using var client = db.CreateClient();
        var store        = new KurrentDBCheckpointStore(client);
        var sessionA     = NewSessionId();
        var sessionB     = NewSessionId();

        var a = await store.CreateCheckpointAsync(sessionA, Payload(new { who = "a" }));
        _     = await store.CreateCheckpointAsync(sessionB, Payload(new { who = "b" }));

        var indexA = (await store.RetrieveIndexAsync(sessionA)).ToList();

        await Assert.That(indexA.Count).IsEqualTo(1);
        await Assert.That(indexA[0].CheckpointId).IsEqualTo(a.CheckpointId);
    }

    [Test]
    public async Task RetrieveCheckpoint_PreservesComplexJsonStructure() {
        using var client = db.CreateClient();
        var store        = new KurrentDBCheckpointStore(client);
        var sessionId    = NewSessionId();

        var nested = Payload(new {
            counter = 7,
            items   = new[] { "x", "y", "z" },
            inner   = new { name = "deep", flag = true },
        });

        var info      = await store.CreateCheckpointAsync(sessionId, nested);
        var retrieved = await store.RetrieveCheckpointAsync(sessionId, info);

        await Assert.That(retrieved.GetProperty("counter").GetInt32()).IsEqualTo(7);
        await Assert.That(retrieved.GetProperty("items").GetArrayLength()).IsEqualTo(3);
        await Assert.That(retrieved.GetProperty("inner").GetProperty("name").GetString()).IsEqualTo("deep");
        await Assert.That(retrieved.GetProperty("inner").GetProperty("flag").GetBoolean()).IsTrue();
    }
}
