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
            KurrentDBClient client,
            string          sessionId,
            Position        fromPosition
        ) {
        var found = new List<(string, JsonDocument)>();

        await foreach (var e in client.ReadAllAsync(Direction.Forwards, fromPosition)) {
            if (!e.Event.EventStreamId.StartsWith("EvalRun-")) continue;
            if (e.Event.EventType is not ("EvalRunStarted" or "TurnScored" or "EvalRunCompleted")) continue;

            var doc = JsonDocument.Parse(e.Event.Data.ToArray());

            var matchesThis = doc.RootElement.TryGetProperty("session_id", out var sid)
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
        await using var client    = db.CreateClient();
        var             sessionId = Guid.NewGuid().ToString("N");

        await SeedSessionAsync(
            client,
            sessionId,
            EventFor(UserMsg("q1", "m-1", 0)),
            EventFor(AsstText("a1", "m-2", 1)),
            EventFor(UserMsg("q2", "m-3", 2)),
            EventFor(AsstText("a2", "m-4", 3))
        );

        var startPos = await SnapshotAllEndAsync(client);

        // Two metrics per turn: Helpfulness fixed-per-turn, Coherence fixed at 0.5.
        var helpfulness = new[] { 1.0, 0.4 };
        var evaluator   = new FixedDualEvaluator(t => helpfulness[t], _ => 0.5);

        var result = await new EvalRunner(client).RunAsync(sessionId, "fixed-dual", "testing", evaluator);

        await Assert.That(result.SessionId).IsEqualTo(sessionId);
        await Assert.That(result.ScoredMetrics.Count).IsEqualTo(4); // 2 turns × 2 metrics
        await Assert.That(result.PerMetricAverage["Helpfulness"]).IsEqualTo(0.7);
        await Assert.That(result.PerMetricAverage["Coherence"]).IsEqualTo(0.5);

        var evts = await ReadEvalEventsForSession(client, sessionId, startPos);

        try {
            // Multiple aggregable metrics → AverageScore stays at 0 (cross-metric mean is meaningless);
            // readers must consult extensions.afw.eval.per_metric_average for the truth.
            var completed = evts.Single(e => e.Type == "EvalRunCompleted").Payload.RootElement;
            await Assert.That(completed.GetProperty("average_score").GetDouble()).IsEqualTo(0.0);
        } finally {
            foreach (var (_, doc) in evts) doc.Dispose();
        }
    }

    [Test]
    public async Task RunAsync_AggregatesTokenTotalsFromTurnMetadata() {
        await using var client    = db.CreateClient();
        var             sessionId = Guid.NewGuid().ToString("N");

        await SeedSessionAsync(
            client,
            sessionId,
            EventFor(UserMsg("q1", "m-1", 0)),
            EventFor(AsstText("a1", "m-2", 1), metadata: Usage(10, 5)),
            EventFor(UserMsg("q2", "m-3", 2)),
            EventFor(AsstText("a2", "m-4", 3), metadata: Usage(20, 8))
        );

        var result = await new EvalRunner(client).RunAsync(
            sessionId,
            "fixed",
            "testing",
            new FixedSingleEvaluator("Helpfulness", 1.0)
        );

        await Assert.That(result.TotalInputTokens).IsEqualTo(30L);
        await Assert.That(result.TotalOutputTokens).IsEqualTo(13L);

        return;

        IDictionary<string, object?> Usage(long input, long output) => new Dictionary<string, object?> {
            ["$usage"] = new Dictionary<string, object?> {
                ["input_tokens"]  = input,
                ["output_tokens"] = output,
            },
        };
    }

    [Test]
    public async Task RunAsync_EmitsEventSequenceToEvalRunStream() {
        await using var client    = db.CreateClient();
        var             sessionId = Guid.NewGuid().ToString("N");

        await SeedSessionAsync(
            client,
            sessionId,
            EventFor(UserMsg("q", "m-1", 0)),
            EventFor(AsstText("a", "m-2", 1))
        );

        var startPos = await SnapshotAllEndAsync(client);

        await new EvalRunner(client).RunAsync(
            sessionId,
            "my-scorer",
            "helpfulness",
            new FixedSingleEvaluator("Helpfulness", 0.9, reason: "solid")
        );

        var evts = await ReadEvalEventsForSession(client, sessionId, startPos);

        try {
            await Assert.That(evts.Select(x => x.Type).ToList())
                .IsEquivalentTo(["EvalRunStarted", "TurnScored", "EvalRunCompleted"]);

            var started = evts[0].Payload.RootElement;
            await Assert.That(started.GetProperty("scorer").GetString()).IsEqualTo("my-scorer");
            await Assert.That(started.GetProperty("criteria").GetString()).IsEqualTo("helpfulness");

            var scored = evts[1].Payload.RootElement;
            await Assert.That(GetIntOrDefault(scored, "turn_index")).IsEqualTo(0);
            await Assert.That(scored.GetProperty("score").GetDouble()).IsEqualTo(0.9);
            await Assert.That(scored.GetProperty("score_label").GetString()).IsEqualTo("Helpfulness");
            await Assert.That(scored.GetProperty("reason").GetString()).IsEqualTo("solid");

            await Assert.That(
                    scored.GetProperty("extensions")
                        .GetProperty("afw")
                        .GetProperty("eval")
                        .GetProperty("metric_kind")
                        .GetString()
                )
                .IsEqualTo("numeric");

            var completed = evts[2].Payload.RootElement;
            await Assert.That(GetIntOrDefault(completed, "turns_scored")).IsEqualTo(1);

            await Assert.That(
                    completed.GetProperty("extensions")
                        .GetProperty("afw")
                        .GetProperty("eval")
                        .GetProperty("per_metric_average")
                        .GetProperty("Helpfulness")
                        .GetDouble()
                )
                .IsEqualTo(0.9);
        } finally {
            foreach (var (_, doc) in evts) doc.Dispose();
        }
    }

    [Test]
    public async Task RunAsync_SessionWithNoTurns_StillWritesStartedAndCompleted() {
        await using var client    = db.CreateClient();
        var             sessionId = Guid.NewGuid().ToString("N");
        var             startPos  = await SnapshotAllEndAsync(client);

        // Missing session stream — reader returns empty.
        var result = await new EvalRunner(client).RunAsync(
            sessionId,
            "noop",
            "none",
            new FixedSingleEvaluator("Helpfulness", 1.0)
        );

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
    public async Task RunAsync_NonNumericMetrics_ExcludedFromAverages_ValuePreservedInExtensions() {
        await using var client    = db.CreateClient();
        var             sessionId = Guid.NewGuid().ToString("N");

        await SeedSessionAsync(
            client,
            sessionId,
            EventFor(UserMsg("q1", "m-1", 0)),
            EventFor(AsstText("a1", "m-2", 1)),
            EventFor(UserMsg("q2", "m-3", 2)),
            EventFor(AsstText("a2", "m-4", 3))
        );

        var startPos = await SnapshotAllEndAsync(client);

        // Returns Verdict (string) + Helpfulness (numeric, null on turn 0, 0.8 on turn 1).
        var evaluator = new MixedKindEvaluator();
        var result    = await new EvalRunner(client).RunAsync(sessionId, "mixed", "criteria", evaluator);

        await Assert.That(result.PerMetricAverage.ContainsKey("Verdict")).IsFalse();
        // Helpfulness null on turn 0 is excluded; turn 1 contributes 0.8.
        await Assert.That(result.PerMetricAverage["Helpfulness"]).IsEqualTo(0.8);
        await Assert.That(result.ScoredMetrics.Count(s => s.MetricName == "Verdict" && !s.IsAggregable)).IsEqualTo(2);

        var evts = await ReadEvalEventsForSession(client, sessionId, startPos);

        try {
            var verdictRows = evts
                .Where(e => e.Type                                                       == "TurnScored")
                .Where(e => e.Payload.RootElement.GetProperty("score_label").GetString() == "Verdict")
                .ToList();

            var firstVerdict = verdictRows[0]
                .Payload.RootElement
                .GetProperty("extensions")
                .GetProperty("afw")
                .GetProperty("eval");
            await Assert.That(firstVerdict.GetProperty("metric_kind").GetString()).IsEqualTo("string");
            await Assert.That(firstVerdict.GetProperty("string_value").GetString()).IsEqualTo("pass");

            var nullNumeric = evts
                .Where(e => e.Type == "TurnScored")
                .Select(e => e.Payload.RootElement)
                .First(e =>
                    e.GetProperty("score_label").GetString() == "Helpfulness"
                 && e.GetProperty("turn_index").GetInt32()   == 0
                 && e.GetProperty("extensions")
                        .GetProperty("afw")
                        .GetProperty("eval")
                        .TryGetProperty("value_missing", out _)
                );

            await Assert.That(
                    nullNumeric.GetProperty("extensions")
                        .GetProperty("afw")
                        .GetProperty("eval")
                        .GetProperty("value_missing")
                        .GetBoolean()
                )
                .IsTrue();

            var completed = evts.Single(e => e.Type == "EvalRunCompleted").Payload.RootElement;
            // Verdict (StringMetric) is non-aggregable, so only Helpfulness ends up in the
            // per-metric averages → run collapses to a single-metric run and AverageScore
            // carries that one metric's average.
            await Assert.That(completed.GetProperty("average_score").GetDouble()).IsEqualTo(0.8);
        } finally {
            foreach (var (_, doc) in evts) doc.Dispose();
        }
    }

    [Test]
    public async Task RunAsync_BooleanMetric_MapsToZeroOrOne_AndAggregates() {
        using var client    = db.CreateClient();
        var       sessionId = Guid.NewGuid().ToString("N");

        await SeedSessionAsync(
            client,
            sessionId,
            EventFor(UserMsg("q1", "m-1", 0)),
            EventFor(AsstText("a1", "m-2", 1)),
            EventFor(UserMsg("q2", "m-3", 2)),
            EventFor(AsstText("a2", "m-4", 3))
        );

        var startPos = await SnapshotAllEndAsync(client);

        // Turn 0 → true (1.0), turn 1 → false (0.0); average = 0.5.
        var values    = new bool?[] { true, false };
        var evaluator = new BooleanEvaluator("Passed", t => values[t]);

        var result = await new EvalRunner(client).RunAsync(sessionId, "bool-scorer", "criteria", evaluator);

        await Assert.That(result.PerMetricAverage["Passed"]).IsEqualTo(0.5);
        await Assert.That(result.ScoredMetrics.All(s => s.IsAggregable)).IsTrue();
        await Assert.That(result.ScoredMetrics.Select(s => s.Score)).IsEquivalentTo(new[] { 1.0, 0.0 });

        var evts = await ReadEvalEventsForSession(client, sessionId, startPos);

        try {
            var rows = evts
                .Where(e => e.Type == "TurnScored")
                .Select(e => e.Payload.RootElement)
                .ToList();

            await Assert.That(rows).Count().IsEqualTo(2);

            foreach (var row in rows) {
                var eval = row.GetProperty("extensions").GetProperty("afw").GetProperty("eval");
                await Assert.That(eval.GetProperty("metric_kind").GetString()).IsEqualTo("boolean");
            }

            var completed = evts.Single(e => e.Type == "EvalRunCompleted").Payload.RootElement;
            // Single aggregable metric → AverageScore carries that metric's average.
            await Assert.That(completed.GetProperty("average_score").GetDouble()).IsEqualTo(0.5);
        } finally {
            foreach (var (_, doc) in evts) doc.Dispose();
        }
    }

    [Test]
    public async Task RunAsync_PassesUniqueCallIdsAndParsedArgumentsToEvaluator() {
        await using var client    = db.CreateClient();
        var             sessionId = Guid.NewGuid().ToString("N");
        var             toolCalls = new AssistantToolCallsGenerated { MessageIndex = 1, Timestamp = Pts };

        var argStruct = new Struct {
            Fields = {
                ["city"] = Value.ForString("London")
            }
        };
        toolCalls.ToolCalls.Add(new ToolCallInfo { CallId = "ignored-1", ToolName = "GetWeather", Arguments = argStruct });
        toolCalls.ToolCalls.Add(new ToolCallInfo { CallId = "ignored-2", ToolName = "GetWeather", Arguments = argStruct });

        await SeedSessionAsync(
            client,
            sessionId,
            EventFor(UserMsg("q", "m-1", 0)),
            EventFor(toolCalls),
            EventFor(
                new ToolResultReceived {
                    CallId       = "ignored-1",
                    ToolName     = "GetWeather",
                    Result       = "Sunny",
                    MessageIndex = 2,
                    Timestamp    = Pts,
                }
            ),
            EventFor(
                new ToolResultReceived {
                    CallId       = "ignored-2",
                    ToolName     = "GetWeather",
                    Result       = "Cloudy",
                    MessageIndex = 3,
                    Timestamp    = Pts,
                }
            ),
            EventFor(AsstText("a", "m-2", 4))
        );

        var capturing = new ChatCapturingEvaluator();
        await new EvalRunner(client).RunAsync(sessionId, "scorer", "criteria", capturing);

        var assistant = capturing.LastResponse!.Messages.Last();
        var calls     = assistant.Contents.OfType<FunctionCallContent>().ToArray();
        var results   = assistant.Contents.OfType<FunctionResultContent>().ToArray();

        await Assert.That(calls.Length).IsEqualTo(2);
        await Assert.That(calls[0].CallId).IsNotEqualTo(calls[1].CallId);
        await Assert.That(calls[0].Arguments?["city"]?.ToString()).IsEqualTo("London");
        await Assert.That(results[0].CallId).IsEqualTo(calls[0].CallId);
        await Assert.That(results[1].CallId).IsEqualTo(calls[1].CallId);
    }

    [Test]
    public async Task RunAsync_SerializesInterpretationAndDiagnosticsUnderExtensions() {
        await using var client    = db.CreateClient();
        var             sessionId = Guid.NewGuid().ToString("N");

        await SeedSessionAsync(
            client,
            sessionId,
            EventFor(UserMsg("q", "m-1", 0)),
            EventFor(AsstText("a", "m-2", 1))
        );

        var startPos = await SnapshotAllEndAsync(client);

        var evaluator = new InterpretingEvaluator(
            "Quality",
            0.4,
            EvaluationRating.Poor,
            failed: true,
            diagnostics: [new EvaluationDiagnostic(EvaluationDiagnosticSeverity.Warning, "missing context")]
        );

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

            return ValueTask.FromResult(
                new EvaluationResult(
                    [
                        new NumericMetric("Helpfulness", first(i)),
                        new NumericMetric("Coherence", second(i)),
                    ]
                )
            );
        }
    }

    sealed class BooleanEvaluator(string name, Func<int, bool?> value) : IEvaluator {
        int _turn;

        public IReadOnlyCollection<string> EvaluationMetricNames { get; } = [name];

        public ValueTask<EvaluationResult> EvaluateAsync(
                IEnumerable<ChatMessage>        messages,
                ChatResponse                    modelResponse,
                ChatConfiguration?              chatConfiguration = null,
                IEnumerable<EvaluationContext>? additionalContext = null,
                CancellationToken               cancellationToken = default
            ) =>
            ValueTask.FromResult(new EvaluationResult(new BooleanMetric(name, value(_turn++))));
    }

    sealed class MixedKindEvaluator : IEvaluator {
        int _turn;

        public IReadOnlyCollection<string> EvaluationMetricNames { get; } = ["Verdict", "Helpfulness"];

        public ValueTask<EvaluationResult> EvaluateAsync(
                IEnumerable<ChatMessage>        messages,
                ChatResponse                    modelResponse,
                ChatConfiguration?              chatConfiguration = null,
                IEnumerable<EvaluationContext>? additionalContext = null,
                CancellationToken               cancellationToken = default
            ) {
            var i = _turn++;
            // Helpfulness intentionally null on turn 0 to exercise the missing-value path.
            double? helpfulness = i == 0 ? null : 0.8;

            return ValueTask.FromResult(
                new EvaluationResult(
                    new StringMetric("Verdict", i == 0 ? "pass" : "fail"),
                    new NumericMetric("Helpfulness", helpfulness)
                )
            );
        }
    }

    sealed class ChatCapturingEvaluator : IEvaluator {
        public ChatResponse? LastResponse { get; private set; }

        public IReadOnlyCollection<string> EvaluationMetricNames { get; } = ["Capture"];

        public ValueTask<EvaluationResult> EvaluateAsync(
                IEnumerable<ChatMessage>        messages,
                ChatResponse                    modelResponse,
                ChatConfiguration?              chatConfiguration = null,
                IEnumerable<EvaluationContext>? additionalContext = null,
                CancellationToken               cancellationToken = default
            ) {
            LastResponse = modelResponse;

            return ValueTask.FromResult(new EvaluationResult(new NumericMetric("Capture", 1.0)));
        }
    }

    sealed class InterpretingEvaluator(
            string                       name,
            double                       score,
            EvaluationRating             rating,
            bool                         failed,
            IList<EvaluationDiagnostic>? diagnostics = null
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
