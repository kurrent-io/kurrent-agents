using System.Runtime.CompilerServices;
using Kurrent.Agent.Schema;
using Kurrent.Agent.Schema.Events;
using Kurrent.AgentFramework.Memory;
using Kurrent.AgentFramework.Projections;
using Kurrent.AgentFramework.Serialization;
using KurrentDB.Client;
using Microsoft.Extensions.Logging.Abstractions;

namespace Kurrent.AgentFramework.IntegrationTests;

/// <summary>
/// Exercises <see cref="FactExtractionService"/> against a real KurrentDB container.
/// Each test scopes its extractor to a per-test marker and uses a unique persistent
/// subscription group so tests are parallel-safe and isolated from events written by
/// other test classes sharing the same container.
/// </summary>
[ClassDataSource<KurrentDbFixture>(Shared = SharedType.PerTestSession)]
public class FactExtractionServiceTests(KurrentDbFixture db) {
    static readonly TimeSpan WaitBudget = TimeSpan.FromSeconds(10);
    static readonly DateTimeOffset Ts = new(2026, 4, 17, 12, 0, 0, TimeSpan.Zero);

    sealed class InMemoryMemory : IAgentMemory {
        readonly List<string> _facts = [];

        public IReadOnlyList<string> Snapshot() {
            lock (_facts) return _facts.ToList();
        }

        public Task RetainAsync(string fact, CancellationToken ct = default) {
            lock (_facts) _facts.Add(fact);
            return Task.CompletedTask;
        }

#pragma warning disable CS1998
        public async IAsyncEnumerable<string> RecallAsync(string query, [EnumeratorCancellation] CancellationToken ct = default) {
            foreach (var f in Snapshot()) yield return f;
        }
#pragma warning restore CS1998
    }

    sealed class Harness : IAsyncDisposable {
        public KurrentDBClient                        Client          { get; }
        public KurrentDBPersistentSubscriptionsClient PsClient        { get; }
        public FactExtractionService                  Service         { get; }
        public InMemoryMemory                         Memory          { get; }
        public List<string>                           ExtractorCalls  { get; }

        public Harness(
            KurrentDBClient                        client,
            KurrentDBPersistentSubscriptionsClient psClient,
            FactExtractionService                  service,
            InMemoryMemory                         memory,
            List<string>                           calls
        ) {
            Client         = client;
            PsClient       = psClient;
            Service        = service;
            Memory         = memory;
            ExtractorCalls = calls;
        }

        public async ValueTask DisposeAsync() {
            await Service.StopAsync(CancellationToken.None);
            Client.Dispose();
            PsClient.Dispose();
        }
    }

    static FactExtractionOptions UniqueOptions() =>
        new(GroupName: $"FactExtraction-{Guid.NewGuid():N}", StartFrom: Position.Start);

    async Task<Harness> StartService(string marker) {
        var client   = db.CreateClient();
        var psClient = db.CreatePersistentClient();
        var memory   = new InMemoryMemory();
        var calls    = new List<string>();

        FactExtractor extractor = content => {
            lock (calls) calls.Add(content);
            return content.Contains(marker) ? [content] : [];
        };

        var service = new FactExtractionService(
            psClient, memory, extractor, UniqueOptions(), NullLogger<FactExtractionService>.Instance
        );
        await service.StartAsync(CancellationToken.None);
        return new Harness(client, psClient, service, memory, calls);
    }

    static IReadOnlyList<string> SnapshotCalls(List<string> calls) {
        lock (calls) return calls.ToList();
    }

    static async Task<bool> WaitUntil(Func<bool> predicate, TimeSpan timeout) {
        var deadline = DateTime.UtcNow + timeout;
        while (DateTime.UtcNow < deadline) {
            if (predicate()) return true;
            await Task.Delay(100);
        }
        return predicate();
    }

    static async Task AppendAsync(KurrentDBClient client, string streamName, params object[] events) {
        await client.AppendToStreamAsync(
            streamName, StreamState.Any, events.Select(e => EventSerializer.Serialize(e)));
    }

    [Test]
    public async Task UserMessage_TriggersFactRetention() {
        var marker        = $"[M-{Guid.NewGuid():N}]";
        var streamName    = StreamNames.AgentSession(Guid.NewGuid().ToString("N"));

        await using var h = await StartService(marker);

        var content = $"{marker} user fact content";
        await AppendAsync(h.Client, streamName,
            new UserMessageReceived(content, "m-1", "user", Ts, 0, Ts));

        var ok = await WaitUntil(() => h.Memory.Snapshot().Contains(content), WaitBudget);

        await Assert.That(ok).IsTrue();
    }

    [Test]
    public async Task NonUserMessageEvents_AreIgnored() {
        var marker       = $"[M-{Guid.NewGuid():N}]";
        var streamName   = StreamNames.AgentSession(Guid.NewGuid().ToString("N"));
        var sentinel     = $"{marker} sentinel";

        await using var h = await StartService(marker);

        // SessionStarted + AssistantTextGenerated embed the marker but aren't
        // UserMessageReceived, so the service must not route them to the extractor.
        await AppendAsync(h.Client, streamName,
            new SessionStarted(
                AppName:           null,
                AgentName:         $"{marker} agent",
                Model:             "model",
                TenantId:          null,
                UserId:            null,
                AgentConfig:       null,
                PreviousSessionId: null,
                Timestamp:         Ts),
            new AssistantTextGenerated($"{marker} assistant", "m-1", "agent", Ts, 0, Ts),
            new UserMessageReceived(sentinel, "m-2", "user", Ts, 1, Ts));

        await WaitUntil(() => h.Memory.Snapshot().Contains(sentinel), WaitBudget);

        var facts = h.Memory.Snapshot();
        var calls = SnapshotCalls(h.ExtractorCalls);
        await Assert.That(facts).Contains(sentinel);
        // The extractor should only have been invoked with the UserMessageReceived content.
        await Assert.That(calls.Any(c => c.Contains("agent"))).IsFalse();
        await Assert.That(calls.Any(c => c.Contains("assistant"))).IsFalse();
    }

    [Test]
    public async Task EmptyUserMessageContent_IsSkippedBeforeExtractor() {
        var marker     = $"[M-{Guid.NewGuid():N}]";
        var streamName = StreamNames.AgentSession(Guid.NewGuid().ToString("N"));
        var sentinel   = $"{marker} sentinel";

        await using var h = await StartService(marker);

        // Two whitespace variants + a sentinel we can wait for. Once the sentinel is
        // observed in memory, any earlier events in the same stream must have already
        // been processed (KurrentDB preserves order within a single stream on $all).
        await AppendAsync(h.Client, streamName,
            new UserMessageReceived("", "m-1", "user", Ts, 0, Ts),
            new UserMessageReceived("   ", "m-2", "user", Ts, 1, Ts),
            new UserMessageReceived(sentinel, "m-3", "user", Ts, 2, Ts));

        await WaitUntil(() => h.Memory.Snapshot().Contains(sentinel), WaitBudget);

        var calls = SnapshotCalls(h.ExtractorCalls);
        await Assert.That(calls).Contains(sentinel);
        await Assert.That(calls).DoesNotContain("");
        await Assert.That(calls).DoesNotContain("   ");
    }

    [Test]
    public async Task EventsOutsideAgentSessionPrefix_AreFilteredServerSide() {
        var marker          = $"[M-{Guid.NewGuid():N}]";
        var agentStream     = StreamNames.AgentSession(Guid.NewGuid().ToString("N"));
        var outsidePrefix   = $"OtherStream-{Guid.NewGuid():N}";
        var sentinelContent = $"{marker} sentinel";
        var outsideContent  = $"{marker} outside";

        await using var h = await StartService(marker);

        // This event matches the event type and contains the marker, but the stream
        // prefix is outside "AgentSession-", so the server-side StreamFilter.Prefix
        // must suppress it.
        await AppendAsync(h.Client, outsidePrefix,
            new UserMessageReceived(outsideContent, "m-1", "user", Ts, 0, Ts));

        await AppendAsync(h.Client, agentStream,
            new UserMessageReceived(sentinelContent, "m-2", "user", Ts, 0, Ts));

        await WaitUntil(() => h.Memory.Snapshot().Contains(sentinelContent), WaitBudget);

        // Give the subscription a touch more time to surface mis-filtered events, then
        // assert the outside-prefix one never made it through.
        await Task.Delay(500);
        await Assert.That(h.Memory.Snapshot()).DoesNotContain(outsideContent);
        var calls = SnapshotCalls(h.ExtractorCalls);
        await Assert.That(calls).DoesNotContain(outsideContent);
    }

    [Test]
    public async Task ExtractorReturningNoFacts_RetainsNothing() {
        var marker     = $"[M-{Guid.NewGuid():N}]";
        var streamName = StreamNames.AgentSession(Guid.NewGuid().ToString("N"));

        await using var h = await StartService(marker);

        // This message does not contain the marker, so the extractor returns zero facts.
        // A separate message with the marker then lets us confirm the service is alive.
        var nonMatching = "unrelated content";
        var sentinel    = $"{marker} proof of life";

        await AppendAsync(h.Client, streamName,
            new UserMessageReceived(nonMatching, "m-1", "user", Ts, 0, Ts),
            new UserMessageReceived(sentinel, "m-2", "user", Ts, 1, Ts));

        await WaitUntil(() => h.Memory.Snapshot().Contains(sentinel), WaitBudget);

        await Assert.That(h.Memory.Snapshot()).DoesNotContain(nonMatching);
    }

    [Test]
    public async Task WhitespaceFactsFromExtractor_AreNotRetained() {
        var marker     = $"[M-{Guid.NewGuid():N}]";
        var streamName = StreamNames.AgentSession(Guid.NewGuid().ToString("N"));

        var client   = db.CreateClient();
        var psClient = db.CreatePersistentClient();
        var memory   = new InMemoryMemory();

        // Extractor returns a whitespace fact alongside a real one — the service must
        // drop the whitespace one.
        FactExtractor extractor = content => content.Contains(marker)
            ? ["   ", content]
            : [];

        var service = new FactExtractionService(
            psClient, memory, extractor, UniqueOptions(), NullLogger<FactExtractionService>.Instance
        );
        await service.StartAsync(CancellationToken.None);

        try {
            var content = $"{marker} real fact";
            await AppendAsync(client, streamName,
                new UserMessageReceived(content, "m-1", "user", Ts, 0, Ts));

            await WaitUntil(() => memory.Snapshot().Contains(content), WaitBudget);

            await Assert.That(memory.Snapshot()).DoesNotContain("   ");
        } finally {
            await service.StopAsync(CancellationToken.None);
            client.Dispose();
            psClient.Dispose();
        }
    }

    [Test]
    public async Task PersistentSubscription_SurvivesServiceRestart() {
        var marker      = $"[M-{Guid.NewGuid():N}]";
        var streamName  = StreamNames.AgentSession(Guid.NewGuid().ToString("N"));
        var options     = UniqueOptions();

        var client   = db.CreateClient();
        var psClient = db.CreatePersistentClient();

        async Task<(InMemoryMemory Memory, FactExtractionService Service)> StartOnce() {
            var mem = new InMemoryMemory();
            FactExtractor ex = content => content.Contains(marker) ? [content] : [];
            var svc = new FactExtractionService(psClient, mem, ex, options, NullLogger<FactExtractionService>.Instance);
            await svc.StartAsync(CancellationToken.None);
            return (mem, svc);
        }

        try {
            // First run: consume and ack the initial fact.
            var first   = $"{marker} first";
            var (mem1, svc1) = await StartOnce();
            try {
                await AppendAsync(client, streamName, new UserMessageReceived(first, "m-1", "user", Ts, 0, Ts));
                await WaitUntil(() => mem1.Snapshot().Contains(first), WaitBudget);
                await Assert.That(mem1.Snapshot()).Contains(first);
            } finally {
                await svc1.StopAsync(CancellationToken.None);
            }

            // Second run with the same group: server checkpoint must prevent replay of `first`
            // while still delivering a new event appended after the restart.
            var second = $"{marker} second";
            var (mem2, svc2) = await StartOnce();
            try {
                await AppendAsync(client, streamName, new UserMessageReceived(second, "m-2", "user", Ts, 1, Ts));
                await WaitUntil(() => mem2.Snapshot().Contains(second), WaitBudget);
                await Assert.That(mem2.Snapshot()).Contains(second);
                await Assert.That(mem2.Snapshot()).DoesNotContain(first);
            } finally {
                await svc2.StopAsync(CancellationToken.None);
            }
        } finally {
            client.Dispose();
            psClient.Dispose();
        }
    }
}
