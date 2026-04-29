using System.Runtime.CompilerServices;
using System.Text.Json;
using Google.Protobuf.WellKnownTypes;
using Kurrent.Agent.Schema;
using Kurrent.Agent.Schema.Events;
using Kurrent.AgentFramework.Eval;
using Kurrent.AgentFramework.Serialization;
using KurrentDB.Client;
using Microsoft.Extensions.AI;

namespace Kurrent.AgentFramework.IntegrationTests;

[ClassDataSource<KurrentDbFixture>(Shared = SharedType.PerTestSession)]
public class EvalRunnerTests(KurrentDbFixture db) {
    static readonly DateTimeOffset Ts  = new(2026, 4, 17, 12, 0, 0, TimeSpan.Zero);
    static readonly Timestamp      Pts = Timestamp.FromDateTimeOffset(Ts);

    static UserMessageReceived UserMsg(string content, string messageId, int idx) =>
        new() {
            Content      = content,
            MessageId    = messageId,
            AuthorName   = "user",
            CreatedAt    = Pts,
            MessageIndex = idx,
            Timestamp    = Pts,
        };

    static AssistantTextGenerated AsstText(string content, string messageId, int idx) =>
        new() {
            Content      = content,
            MessageId    = messageId,
            AuthorName   = "agent",
            CreatedAt    = Pts,
            MessageIndex = idx,
            Timestamp    = Pts,
        };

    static async Task SeedSessionAsync(KurrentDBClient client, string sessionId, params EventData[] events) {
        await client.AppendToStreamAsync(StreamNames.AgentSession(sessionId), StreamState.Any, events);
    }

    static EventData EventFor(object @event, IDictionary<string, object?>? metadata = null) =>
        EventSerializer.Serialize(@event, metadata: metadata);

    /// <summary>
    /// Snapshot the current end of <c>$all</c> so a follow-up forward read only scans
    /// events written after this call. Avoids O(history) scans on the shared container.
    /// </summary>
    static async Task<Position> SnapshotAllEndAsync(KurrentDBClient client) {
        await foreach (var e in client.ReadAllAsync(Direction.Backwards, Position.End, maxCount: 1)) {
            return e.OriginalPosition ?? Position.Start;
        }
        return Position.Start;
    }

    /// <summary>
    /// Read eval events for the given session starting from a known <see cref="Position"/>,
    /// stopping as soon as this session's <c>EvalRunCompleted</c> is seen. The eval stream
    /// id is a random guid that <see cref="EvalRunner"/> does not return, so events are
    /// matched by the <c>session_id</c> carried in each event's payload. Bounding by start
    /// position keeps the scan O(events-written-during-this-test) regardless of container age.
    /// </summary>
    static int GetIntOrDefault(JsonElement element, string property) =>
        element.TryGetProperty(property, out var v) && v.ValueKind == JsonValueKind.Number ? v.GetInt32() : 0;

    static async Task<List<(string Type, JsonDocument Payload)>> ReadEvalEventsForSession(
        KurrentDBClient client, string sessionId, Position fromPosition
    ) {
        var found = new List<(string, JsonDocument)>();

        await foreach (var e in client.ReadAllAsync(Direction.Forwards, fromPosition)) {
            if (!e.Event.EventStreamId.StartsWith("EvalRun-")) continue;
            if (e.Event.EventType is not ("EvalRunStarted" or "TurnScored" or "EvalRunCompleted")) continue;

            var doc          = JsonDocument.Parse(e.Event.Data.ToArray());
            var matchesThis  = doc.RootElement.TryGetProperty("session_id", out var sid)
                            && sid.GetString() == sessionId;

            if (matchesThis) {
                found.Add((e.Event.EventType, doc));
                if (e.Event.EventType == "EvalRunCompleted") break;
            } else {
                doc.Dispose();
            }
        }

        return found;
    }

    [Test]
    public async Task RunAsync_ComputesAverageScoreAndReturnsScoredTurns() {
        using var client = db.CreateClient();
        var sessionId    = Guid.NewGuid().ToString("N");

        await SeedSessionAsync(client, sessionId,
            EventFor(UserMsg("q1", "m-1", 0)),
            EventFor(AsstText("a1", "m-2", 1)),
            EventFor(UserMsg("q2", "m-3", 2)),
            EventFor(AsstText("a2", "m-4", 3)));

        var runner = new EvalRunner(client);

        // Fixed-score scorer: turn 0 -> 1.0, turn 1 -> 0.5 => avg 0.75
        var scores  = new[] { 1.0, 0.5 };
        var labels  = new[] { "good", "acceptable" };
        var reasons = new[] { "perfect", "could be better" };

        var result = await runner.RunAsync(
            sessionId: sessionId,
            scorerName: "fixed",
            criteria: "testing",
            scorer: (turn, _) => Task.FromResult(
                new ScoredTurn(turn, scores[turn.Index], labels[turn.Index], reasons[turn.Index])));

        await Assert.That(result.SessionId).IsEqualTo(sessionId);
        await Assert.That(result.ScoredTurns.Count).IsEqualTo(2);
        await Assert.That(result.AverageScore).IsEqualTo(0.75);
        await Assert.That(result.ScoredTurns[0].Score).IsEqualTo(1.0);
        await Assert.That(result.ScoredTurns[1].Score).IsEqualTo(0.5);
    }

    [Test]
    public async Task RunAsync_AggregatesTokenTotalsFromTurnMetadata() {
        using var client = db.CreateClient();
        var sessionId    = Guid.NewGuid().ToString("N");

        IDictionary<string, object?> Usage(long input, long output) => new Dictionary<string, object?> {
            ["$usage"] = new Dictionary<string, object?> {
                ["input_tokens"]  = input,
                ["output_tokens"] = output,
            },
        };

        await SeedSessionAsync(client, sessionId,
            EventFor(UserMsg("q1", "m-1", 0)),
            EventFor(AsstText("a1", "m-2", 1), metadata: Usage(10, 5)),
            EventFor(UserMsg("q2", "m-3", 2)),
            EventFor(AsstText("a2", "m-4", 3), metadata: Usage(20, 8)));

        var result = await new EvalRunner(client).RunAsync(
            sessionId, "fixed", "testing", (t, _) => Task.FromResult(new ScoredTurn(t, 1.0, null, null)));

        await Assert.That(result.TotalInputTokens).IsEqualTo(30L);
        await Assert.That(result.TotalOutputTokens).IsEqualTo(13L);
    }

    [Test]
    public async Task RunAsync_EmitsEventSequenceToEvalRunStream() {
        using var client = db.CreateClient();
        var sessionId    = Guid.NewGuid().ToString("N");

        await SeedSessionAsync(client, sessionId,
            EventFor(UserMsg("q", "m-1", 0)),
            EventFor(AsstText("a", "m-2", 1)));

        var startPos = await SnapshotAllEndAsync(client);

        await new EvalRunner(client).RunAsync(
            sessionId, "my-scorer", "helpfulness",
            (t, _) => Task.FromResult(new ScoredTurn(t, 0.9, "good", "solid")));

        var evts = await ReadEvalEventsForSession(client, sessionId, startPos);
        try {
            await Assert.That(evts.Select(x => x.Type).ToList())
                .IsEquivalentTo(new[] { "EvalRunStarted", "TurnScored", "EvalRunCompleted" });

            var started = evts[0].Payload.RootElement;
            await Assert.That(started.GetProperty("scorer").GetString()).IsEqualTo("my-scorer");
            await Assert.That(started.GetProperty("criteria").GetString()).IsEqualTo("helpfulness");

            var scored = evts[1].Payload.RootElement;
            await Assert.That(GetIntOrDefault(scored, "turn_index")).IsEqualTo(0);
            await Assert.That(scored.GetProperty("score").GetDouble()).IsEqualTo(0.9);
            await Assert.That(scored.GetProperty("score_label").GetString()).IsEqualTo("good");

            var completed = evts[2].Payload.RootElement;
            await Assert.That(GetIntOrDefault(completed, "turns_scored")).IsEqualTo(1);
            await Assert.That(completed.GetProperty("average_score").GetDouble()).IsEqualTo(0.9);
        } finally {
            foreach (var (_, doc) in evts) doc.Dispose();
        }
    }

    [Test]
    public async Task RunAsync_SessionWithNoTurns_StillWritesStartedAndCompleted() {
        using var client = db.CreateClient();
        var sessionId    = Guid.NewGuid().ToString("N");

        var startPos = await SnapshotAllEndAsync(client);

        // Missing session stream — reader returns empty.
        var result = await new EvalRunner(client).RunAsync(
            sessionId, "noop", "none", (t, _) => Task.FromResult(new ScoredTurn(t, 1.0, null, null)));

        await Assert.That(result.ScoredTurns).IsEmpty();
        await Assert.That(result.AverageScore).IsEqualTo(0.0);

        var evts = await ReadEvalEventsForSession(client, sessionId, startPos);
        try {
            await Assert.That(evts.Select(x => x.Type).ToList())
                .IsEquivalentTo(new[] { "EvalRunStarted", "EvalRunCompleted" });
        } finally {
            foreach (var (_, doc) in evts) doc.Dispose();
        }
    }

    // --- LlmJudge factory ---

    sealed class StubChatClient(string responseText) : IChatClient {
        public Task<ChatResponse> GetResponseAsync(
                IEnumerable<ChatMessage> messages,
                ChatOptions?             options           = null,
                CancellationToken        cancellationToken = default
            ) => Task.FromResult(new ChatResponse(new ChatMessage(ChatRole.Assistant, responseText)));

#pragma warning disable CS1998
        public async IAsyncEnumerable<ChatResponseUpdate> GetStreamingResponseAsync(
                IEnumerable<ChatMessage>                   messages,
                ChatOptions?                               options           = null,
                [EnumeratorCancellation] CancellationToken cancellationToken = default
            ) {
            yield return new ChatResponseUpdate(ChatRole.Assistant, responseText);
        }
#pragma warning restore CS1998

        public object? GetService(System.Type serviceType, object? serviceKey = null) => null;
        public void Dispose() { }
    }

    [Test]
    public async Task LlmJudge_ParsesValidJsonResponse() {
        var client = new StubChatClient("""{"score": 0.8, "label": "good", "reason": "clear"}""");
        var judge  = EvalRunner.LlmJudge(client, "accuracy");

        var turn   = new Turn(0, "q", "a", [], null, null);
        var scored = await judge(turn, CancellationToken.None);

        await Assert.That(scored.Score).IsEqualTo(0.8);
        await Assert.That(scored.Label).IsEqualTo("good");
        await Assert.That(scored.Reason).IsEqualTo("clear");
    }

    [Test]
    public async Task LlmJudge_MalformedJson_FallsBackToHalfScore() {
        var client = new StubChatClient("not JSON at all");
        var judge  = EvalRunner.LlmJudge(client, "accuracy");

        var turn   = new Turn(0, "q", "a", [], null, null);
        var scored = await judge(turn, CancellationToken.None);

        await Assert.That(scored.Score).IsEqualTo(0.5);
        await Assert.That(scored.Label).IsEqualTo("parse_error");
        await Assert.That(scored.Reason).IsNotNull();
    }

    [Test]
    public async Task LlmJudge_MissingOptionalFields_ScoreStillReadable() {
        var client = new StubChatClient("""{"score": 0.42}""");
        var judge  = EvalRunner.LlmJudge(client, "accuracy");

        var turn   = new Turn(0, "q", "a", [], null, null);
        var scored = await judge(turn, CancellationToken.None);

        await Assert.That(scored.Score).IsEqualTo(0.42);
        await Assert.That(scored.Label).IsNull();
        await Assert.That(scored.Reason).IsNull();
    }
}
