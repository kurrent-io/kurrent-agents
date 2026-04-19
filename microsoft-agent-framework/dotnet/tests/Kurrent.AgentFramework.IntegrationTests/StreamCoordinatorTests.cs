using System.Text.Json;
using Kurrent.AgentFramework.Workflows;
using KurrentDB.Client;

namespace Kurrent.AgentFramework.IntegrationTests;

[ClassDataSource<KurrentDbFixture>(Shared = SharedType.PerTestSession)]
public class StreamCoordinatorTests(KurrentDbFixture db) {
    sealed record WorkItem(string TaskId, string Payload);

    static string NewStream() => $"Coord-{Guid.NewGuid():N}";

    [Test]
    public async Task PublishAsync_AppendsEventWithTypeAndPayload() {
        using var client = db.CreateClient();
        var coord        = new StreamCoordinator(client);
        var stream       = NewStream();
        var item         = new WorkItem("t-1", "do-thing");

        await coord.PublishAsync(stream, "WorkItemAssigned", item);

        var read = await client
            .ReadStreamAsync(Direction.Forwards, stream, StreamPosition.Start, maxCount: 1)
            .SingleAsync();

        await Assert.That(read.Event.EventType).IsEqualTo("WorkItemAssigned");
        var decoded = JsonSerializer.Deserialize<WorkItem>(
            read.Event.Data.Span,
            new JsonSerializerOptions { PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower });
        await Assert.That(decoded).IsEqualTo(item);
    }

    [Test]
    public async Task PublishResultAsync_StoresCorrelationIdInMetadata() {
        using var client   = db.CreateClient();
        var coord          = new StreamCoordinator(client);
        var stream         = NewStream();
        var correlationId  = Guid.NewGuid().ToString("N");

        await coord.PublishResultAsync(stream, correlationId, new { ok = true });

        var read = await client
            .ReadStreamAsync(Direction.Forwards, stream, StreamPosition.Start, maxCount: 1)
            .SingleAsync();

        await Assert.That(read.Event.EventType).IsEqualTo("AgentResult");
        var metadata = JsonSerializer.Deserialize<JsonElement>(read.Event.Metadata.Span);
        await Assert.That(metadata.GetProperty("$correlationId").GetString()).IsEqualTo(correlationId);
    }

    [Test]
    public async Task SubscribeAsync_DeliversEventsToHandlerInOrder() {
        using var client = db.CreateClient();
        var coord        = new StreamCoordinator(client);
        var stream       = NewStream();
        var received     = new List<(string Type, WorkItem Payload)>();

        using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(15));

        // Start the subscription in the background — it runs until we cancel.
        var subscriptionTask = Task.Run(async () => {
            try {
                await coord.SubscribeAsync<WorkItem>(stream, (payload, eventType, _) => {
                    lock (received) received.Add((eventType, payload));
                    return Task.CompletedTask;
                }, cts.Token);
            } catch (OperationCanceledException) { }
        }, cts.Token);

        await coord.PublishAsync(stream, "Assigned", new WorkItem("t-1", "one"));
        await coord.PublishAsync(stream, "Assigned", new WorkItem("t-2", "two"));
        await coord.PublishAsync(stream, "Completed", new WorkItem("t-3", "three"));

        var deadline = DateTime.UtcNow.AddSeconds(10);
        while (DateTime.UtcNow < deadline) {
            lock (received) if (received.Count >= 3) break;
            await Task.Delay(100, cts.Token);
        }

        cts.Cancel();
        try { await subscriptionTask; } catch { }

        List<(string Type, WorkItem Payload)> snapshot;
        lock (received) snapshot = received.ToList();

        await Assert.That(snapshot.Count).IsEqualTo(3);
        await Assert.That(snapshot[0].Type).IsEqualTo("Assigned");
        await Assert.That(snapshot[0].Payload.TaskId).IsEqualTo("t-1");
        await Assert.That(snapshot[1].Payload.TaskId).IsEqualTo("t-2");
        await Assert.That(snapshot[2].Type).IsEqualTo("Completed");
    }

    [Test]
    public async Task PublishAsync_MultipleEvents_PreservesOrder() {
        using var client = db.CreateClient();
        var coord        = new StreamCoordinator(client);
        var stream       = NewStream();

        for (var i = 0; i < 5; i++) {
            await coord.PublishAsync(stream, "Tick", new { n = i });
        }

        var events = await client
            .ReadStreamAsync(Direction.Forwards, stream, StreamPosition.Start)
            .ToListAsync();

        await Assert.That(events.Count).IsEqualTo(5);
        for (var i = 0; i < 5; i++) {
            var doc = JsonDocument.Parse(events[i].Event.Data);
            await Assert.That(doc.RootElement.GetProperty("n").GetInt32()).IsEqualTo(i);
        }
    }
}
