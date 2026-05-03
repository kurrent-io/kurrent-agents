using System.Text.Json;
using Google.Protobuf.WellKnownTypes;
using Kurrent.Agent.Schema;
using Kurrent.Agent.Schema.Events;
using Kurrent.AgentFramework.Eval;
using Kurrent.AgentFramework.Serialization;
using KurrentDB.Client;
using Microsoft.Extensions.AI;
using Microsoft.Extensions.AI.Evaluation;

namespace Kurrent.AgentFramework.IntegrationTests;

[ClassDataSource<KurrentDbFixture>(Shared = SharedType.PerTestSession)]
public class SessionEvalRunnerTests(KurrentDbFixture db) {
    static readonly DateTimeOffset Ts  = new(2026, 5, 3, 12, 0, 0, TimeSpan.Zero);
    static readonly Timestamp      Pts = Timestamp.FromDateTimeOffset(Ts);

    static EventData EventFor(object @event, IDictionary<string, object?>? metadata = null) =>
        EventSerializer.Serialize(@event, metadata: metadata);

    static async Task SeedSessionAsync(KurrentDBClient client, string sessionId, params EventData[] events) {
        await client.AppendToStreamAsync(StreamNames.AgentSession(sessionId), StreamState.Any, events);
    }

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

    static async Task<Position> SnapshotAllEndAsync(KurrentDBClient client) {
        await foreach (var e in client.ReadAllAsync(Direction.Backwards, Position.End, maxCount: 1)) {
            return e.OriginalPosition ?? Position.Start;
        }
        return Position.Start;
    }

    static async Task<List<(string Type, JsonDocument Payload)>> ReadEvalEventsForSession(
        KurrentDBClient client, string sessionId, Position fromPosition
    ) {
        var found = new List<(string, JsonDocument)>();

        await foreach (var e in client.ReadAllAsync(Direction.Forwards, fromPosition)) {
            if (!e.Event.EventStreamId.StartsWith("EvalRun-")) continue;
            if (e.Event.EventType is not ("EvalRunStarted" or "SessionScored" or "EvalRunCompleted")) continue;

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
    public async Task RunSessionAsync_EmptySession_AppendsStartedAndCompletedNoSessionScored() {
        using var client = db.CreateClient();
        var sessionId    = Guid.NewGuid().ToString("N");
        // Note: stream not seeded — the AgentSession-{id} stream does not exist.

        var startPos = await SnapshotAllEndAsync(client);

        var evaluator = new FixedSessionEvaluator(0.42, "Helpfulness");

        var result = await new EvalRunner(client).RunSessionAsync(
            sessionId,
            scorerName: "test-scorer",
            criteria: "test",
            evaluator: evaluator);

        await Assert.That(result.SessionId).IsEqualTo(sessionId);
        await Assert.That(result.ScoredMetrics).IsEmpty();
        await Assert.That(result.PerMetricAverage).IsEmpty();
        await Assert.That(evaluator.CallCount).IsEqualTo(0);

        var events = await ReadEvalEventsForSession(client, sessionId, startPos);
        try {
            await Assert.That(events.Count).IsEqualTo(2);
            await Assert.That(events[0].Type).IsEqualTo("EvalRunStarted");
            await Assert.That(events[1].Type).IsEqualTo("EvalRunCompleted");
        } finally {
            foreach (var (_, doc) in events) doc.Dispose();
        }
    }

    [Test]
    public async Task RunSessionAsync_FlattenedMessages_PreservesUserAssistantOrder() {
        using var client = db.CreateClient();
        var sessionId    = Guid.NewGuid().ToString("N");

        await SeedSessionAsync(client, sessionId,
            EventFor(UserMsg("first user", "m-1", 0)),
            EventFor(AsstText("first assistant", "m-2", 1)),
            EventFor(UserMsg("second user", "m-3", 2)),
            EventFor(AsstText("second assistant", "m-4", 3)));

        var evaluator = new FixedSessionEvaluator(1.0, "Helpfulness");

        await new EvalRunner(client).RunSessionAsync(
            sessionId, "test-scorer", "test", evaluator);

        await Assert.That(evaluator.CallCount).IsEqualTo(1);
        await Assert.That(evaluator.LastMessages).IsNotNull();
        await Assert.That(evaluator.LastMessages!.Count).IsEqualTo(4);

        await Assert.That(evaluator.LastMessages[0].Role).IsEqualTo(ChatRole.User);
        await Assert.That(evaluator.LastMessages[0].Text).IsEqualTo("first user");

        await Assert.That(evaluator.LastMessages[1].Role).IsEqualTo(ChatRole.Assistant);
        await Assert.That(evaluator.LastMessages[1].Text).IsEqualTo("first assistant");

        await Assert.That(evaluator.LastMessages[2].Role).IsEqualTo(ChatRole.User);
        await Assert.That(evaluator.LastMessages[2].Text).IsEqualTo("second user");

        await Assert.That(evaluator.LastMessages[3].Role).IsEqualTo(ChatRole.Assistant);
        await Assert.That(evaluator.LastMessages[3].Text).IsEqualTo("second assistant");

        await Assert.That(evaluator.LastResponse!.Messages.Count).IsGreaterThan(0);
        await Assert.That(evaluator.LastResponse.Messages[0].Text).IsEqualTo("second assistant");
    }

    [Test]
    public async Task RunSessionAsync_NumericMetric_EmitsOneSessionScoredWithCorrectScore() {
        using var client = db.CreateClient();
        var sessionId    = Guid.NewGuid().ToString("N");

        await SeedSessionAsync(client, sessionId,
            EventFor(UserMsg("question", "m-1", 0)),
            EventFor(AsstText("answer", "m-2", 1)));

        var startPos = await SnapshotAllEndAsync(client);

        var evaluator = new FixedSessionEvaluator(0.75, "Helpfulness");

        var result = await new EvalRunner(client).RunSessionAsync(
            sessionId, "test-scorer", "test", evaluator);

        await Assert.That(result.ScoredMetrics.Count).IsEqualTo(1);
        var scored = result.ScoredMetrics[0];
        await Assert.That(scored.Turn).IsNull();
        await Assert.That(scored.MetricName).IsEqualTo("Helpfulness");
        await Assert.That(scored.Score).IsEqualTo(0.75);
        await Assert.That(scored.MetricKind).IsEqualTo("numeric");
        await Assert.That(scored.IsAggregable).IsTrue();

        await Assert.That(result.PerMetricAverage.Count).IsEqualTo(1);
        await Assert.That(result.PerMetricAverage["Helpfulness"]).IsEqualTo(0.75);

        var events = await ReadEvalEventsForSession(client, sessionId, startPos);
        try {
            await Assert.That(events.Count).IsEqualTo(3);
            await Assert.That(events[0].Type).IsEqualTo("EvalRunStarted");
            await Assert.That(events[1].Type).IsEqualTo("SessionScored");
            await Assert.That(events[2].Type).IsEqualTo("EvalRunCompleted");

            var completedJson = events[2].Payload.RootElement;
            await Assert.That(completedJson.GetProperty("average_score").GetDouble()).IsEqualTo(0.75);

            var scoredJson = events[1].Payload.RootElement;
            await Assert.That(scoredJson.GetProperty("score").GetDouble()).IsEqualTo(0.75);
            await Assert.That(scoredJson.GetProperty("score_label").GetString()).IsEqualTo("Helpfulness");

            var afwEval = scoredJson.GetProperty("extensions").GetProperty("afw").GetProperty("eval");
            await Assert.That(afwEval.GetProperty("metric_kind").GetString()).IsEqualTo("numeric");
        } finally {
            foreach (var (_, doc) in events) doc.Dispose();
        }
    }

    /// <summary>
    /// Test double — records the call it received and returns a single
    /// <see cref="NumericMetric"/> with the configured score and name.
    /// </summary>
    sealed class FixedSessionEvaluator(double score, string metricName) : IEvaluator {
        public IReadOnlyCollection<string> EvaluationMetricNames { get; } = [metricName];

        public IList<ChatMessage>? LastMessages { get; private set; }
        public ChatResponse?       LastResponse { get; private set; }
        public int                 CallCount    { get; private set; }

        public ValueTask<EvaluationResult> EvaluateAsync(
                IEnumerable<ChatMessage>        messages,
                ChatResponse                    modelResponse,
                ChatConfiguration?              chatConfiguration = null,
                IEnumerable<EvaluationContext>? additionalContext = null,
                CancellationToken               cancellationToken = default
            ) {
            LastMessages = messages.ToList();
            LastResponse = modelResponse;
            CallCount++;
            return ValueTask.FromResult(new EvaluationResult(new NumericMetric(metricName, score)));
        }
    }
}
