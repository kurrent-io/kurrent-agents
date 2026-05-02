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
    public async Task RunAsync_EmitsOneTurnScoredPerMetricPerTurn_AndPerMetricAverages() {
        using var client = db.CreateClient();
        var sessionId    = Guid.NewGuid().ToString("N");

        await SeedSessionAsync(client, sessionId,
            EventFor(UserMsg("q1", "m-1", 0)),
            EventFor(AsstText("a1", "m-2", 1)),
            EventFor(UserMsg("q2", "m-3", 2)),
            EventFor(AsstText("a2", "m-4", 3)));

        // Two metrics per turn: Helpfulness fixed-per-turn, Coherence fixed at 0.5.
        var helpfulness = new[] { 1.0, 0.4 };
        var evaluator   = new FixedDualEvaluator(t => helpfulness[t], _ => 0.5);

        var result = await new EvalRunner(client).RunAsync(
            sessionId, "fixed-dual", "testing", evaluator);

        await Assert.That(result.SessionId).IsEqualTo(sessionId);
        await Assert.That(result.ScoredMetrics.Count).IsEqualTo(4); // 2 turns × 2 metrics
        await Assert.That(result.PerMetricAverage["Helpfulness"]).IsEqualTo(0.7);
        await Assert.That(result.PerMetricAverage["Coherence"]).IsEqualTo(0.5);
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
            sessionId, "fixed", "testing", new FixedSingleEvaluator("Helpfulness", 1.0));

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
            new FixedSingleEvaluator("Helpfulness", 0.9, reason: "solid"));

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
            await Assert.That(scored.GetProperty("score_label").GetString()).IsEqualTo("Helpfulness");
            await Assert.That(scored.GetProperty("reason").GetString()).IsEqualTo("solid");
            await Assert.That(scored.GetProperty("extensions")
                .GetProperty("afw").GetProperty("eval").GetProperty("metric_kind").GetString()).IsEqualTo("numeric");

            var completed = evts[2].Payload.RootElement;
            await Assert.That(GetIntOrDefault(completed, "turns_scored")).IsEqualTo(1);
            await Assert.That(completed.GetProperty("extensions")
                .GetProperty("afw").GetProperty("eval").GetProperty("per_metric_average")
                .GetProperty("Helpfulness").GetDouble()).IsEqualTo(0.9);
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
            sessionId, "noop", "none", new FixedSingleEvaluator("Helpfulness", 1.0));

        await Assert.That(result.ScoredMetrics).IsEmpty();
        await Assert.That(result.PerMetricAverage).IsEmpty();

        var evts = await ReadEvalEventsForSession(client, sessionId, startPos);
        try {
            await Assert.That(evts.Select(x => x.Type).ToList())
                .IsEquivalentTo(new[] { "EvalRunStarted", "EvalRunCompleted" });
        } finally {
            foreach (var (_, doc) in evts) doc.Dispose();
        }
    }

    [Test]
    public async Task RunAsync_SerializesInterpretationAndDiagnosticsUnderExtensions() {
        using var client = db.CreateClient();
        var sessionId    = Guid.NewGuid().ToString("N");

        await SeedSessionAsync(client, sessionId,
            EventFor(UserMsg("q", "m-1", 0)),
            EventFor(AsstText("a", "m-2", 1)));

        var startPos = await SnapshotAllEndAsync(client);

        var evaluator = new InterpretingEvaluator(
            "Quality", 0.4, EvaluationRating.Poor, failed: true,
            diagnostics: [new EvaluationDiagnostic(EvaluationDiagnosticSeverity.Warning, "missing context")]);

        await new EvalRunner(client).RunAsync(sessionId, "interp", "criteria", evaluator);

        var evts = await ReadEvalEventsForSession(client, sessionId, startPos);
        try {
            var scored = evts.Single(e => e.Type == "TurnScored").Payload.RootElement;
            var eval   = scored.GetProperty("extensions").GetProperty("afw").GetProperty("eval");

            await Assert.That(eval.GetProperty("metric_kind").GetString()).IsEqualTo("numeric");
            await Assert.That(eval.GetProperty("interpretation").GetProperty("rating").GetString()).IsEqualTo("Poor");
            await Assert.That(eval.GetProperty("interpretation").GetProperty("failed").GetBoolean()).IsTrue();
            await Assert.That(eval.GetProperty("diagnostics")[0].GetString()).Contains("missing context");
        } finally {
            foreach (var (_, doc) in evts) doc.Dispose();
        }
    }

    sealed class FixedSingleEvaluator(string name, double score, string? reason = null) : IEvaluator {
        public IReadOnlyCollection<string> EvaluationMetricNames { get; } = [name];

        public ValueTask<EvaluationResult> EvaluateAsync(
                IEnumerable<ChatMessage>        messages,
                ChatResponse                    modelResponse,
                ChatConfiguration?              chatConfiguration = null,
                IEnumerable<EvaluationContext>? additionalContext = null,
                CancellationToken               cancellationToken = default
            ) =>
            ValueTask.FromResult(new EvaluationResult(new NumericMetric(name, score, reason)));
    }

    sealed class FixedDualEvaluator(Func<int, double> first, Func<int, double> second) : IEvaluator {
        int _turn;

        public IReadOnlyCollection<string> EvaluationMetricNames { get; } = ["Helpfulness", "Coherence"];

        public ValueTask<EvaluationResult> EvaluateAsync(
                IEnumerable<ChatMessage>        messages,
                ChatResponse                    modelResponse,
                ChatConfiguration?              chatConfiguration = null,
                IEnumerable<EvaluationContext>? additionalContext = null,
                CancellationToken               cancellationToken = default
            ) {
            var i = _turn++;
            return ValueTask.FromResult(new EvaluationResult([
                new NumericMetric("Helpfulness", first(i)),
                new NumericMetric("Coherence",   second(i)),
            ]));
        }
    }

    sealed class InterpretingEvaluator(
            string                            name,
            double                            score,
            EvaluationRating                  rating,
            bool                              failed,
            IList<EvaluationDiagnostic>?      diagnostics = null
        ) : IEvaluator {
        public IReadOnlyCollection<string> EvaluationMetricNames { get; } = [name];

        public ValueTask<EvaluationResult> EvaluateAsync(
                IEnumerable<ChatMessage>        messages,
                ChatResponse                    modelResponse,
                ChatConfiguration?              chatConfiguration = null,
                IEnumerable<EvaluationContext>? additionalContext = null,
                CancellationToken               cancellationToken = default
            ) {
            var metric = new NumericMetric(name, score) {
                Interpretation = new(rating, failed),
                Diagnostics    = diagnostics,
            };
            return ValueTask.FromResult(new EvaluationResult(metric));
        }
    }
}
