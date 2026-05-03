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

    [Test]
    public async Task RunSessionAsync_CompositeEvaluator_MixedMetricKinds_AllPersistedCorrectly() {
        using var client = db.CreateClient();
        var sessionId    = Guid.NewGuid().ToString("N");

        await SeedSessionAsync(client, sessionId,
            EventFor(UserMsg("q", "m-1", 0)),
            EventFor(AsstText("a", "m-2", 1)));

        var startPos = await SnapshotAllEndAsync(client);

        var composite = new CompositeEvaluator(
            new FixedSessionEvaluator(0.7,  "Numeric"),
            new BooleanSessionEvaluator(true, "Boolean"),
            new StringSessionEvaluator("good", "String"),
            new NullNumericSessionEvaluator("MissingNumeric"));

        var result = await new EvalRunner(client).RunSessionAsync(
            sessionId, "test-scorer", "test", composite);

        await Assert.That(result.ScoredMetrics.Count).IsEqualTo(4);

        var byName = result.ScoredMetrics.ToDictionary(s => s.MetricName);

        await Assert.That(byName["Numeric"].MetricKind).IsEqualTo("numeric");
        await Assert.That(byName["Numeric"].IsAggregable).IsTrue();
        await Assert.That(byName["Numeric"].Score).IsEqualTo(0.7);

        await Assert.That(byName["Boolean"].MetricKind).IsEqualTo("boolean");
        await Assert.That(byName["Boolean"].IsAggregable).IsTrue();
        await Assert.That(byName["Boolean"].Score).IsEqualTo(1.0);

        await Assert.That(byName["String"].MetricKind).IsEqualTo("string");
        await Assert.That(byName["String"].IsAggregable).IsFalse();
        await Assert.That(byName["String"].StringValue).IsEqualTo("good");

        await Assert.That(byName["MissingNumeric"].MetricKind).IsEqualTo("numeric");
        await Assert.That(byName["MissingNumeric"].IsAggregable).IsFalse();

        // PerMetricAverage only includes aggregable metrics — Numeric (0.7) + Boolean (1.0) → 2 entries.
        await Assert.That(result.PerMetricAverage.Count).IsEqualTo(2);
        await Assert.That(result.PerMetricAverage["Numeric"]).IsEqualTo(0.7);
        await Assert.That(result.PerMetricAverage["Boolean"]).IsEqualTo(1.0);

        var events = await ReadEvalEventsForSession(client, sessionId, startPos);
        try {
            // 1 EvalRunStarted + 4 SessionScored + 1 EvalRunCompleted = 6
            await Assert.That(events.Count).IsEqualTo(6);
            await Assert.That(events.Count(e => e.Type == "SessionScored")).IsEqualTo(4);
        } finally {
            foreach (var (_, doc) in events) doc.Dispose();
        }
    }

    [Test]
    public async Task RunSessionAsync_TokenTotals_SumUsageMetadataAcrossAssistantEvents() {
        using var client = db.CreateClient();
        var sessionId    = Guid.NewGuid().ToString("N");

        var usageMeta1 = new Dictionary<string, object?> {
            ["$usage"] = new Dictionary<string, object?> { ["input_tokens"] = 100L, ["output_tokens"] = 50L }
        };
        var usageMeta2 = new Dictionary<string, object?> {
            ["$usage"] = new Dictionary<string, object?> { ["input_tokens"] = 200L, ["output_tokens"] = 75L }
        };

        await SeedSessionAsync(client, sessionId,
            EventFor(UserMsg("q1", "m-1", 0)),
            EventFor(AsstText("a1", "m-2", 1), usageMeta1),
            EventFor(UserMsg("q2", "m-3", 2)),
            EventFor(AsstText("a2", "m-4", 3), usageMeta2));

        var result = await new EvalRunner(client).RunSessionAsync(
            sessionId, "test-scorer", "test",
            new FixedSessionEvaluator(1.0, "Helpfulness"));

        await Assert.That(result.TotalInputTokens).IsEqualTo(300L);
        await Assert.That(result.TotalOutputTokens).IsEqualTo(125L);
    }

    [Test]
    public async Task RunSessionAsync_AssistantOnlySession_StillFlattens() {
        using var client = db.CreateClient();
        var sessionId    = Guid.NewGuid().ToString("N");

        await SeedSessionAsync(client, sessionId,
            EventFor(AsstText("standalone", "m-1", 0)));

        var evaluator = new FixedSessionEvaluator(0.5, "Helpfulness");

        await new EvalRunner(client).RunSessionAsync(
            sessionId, "test-scorer", "test", evaluator);

        await Assert.That(evaluator.LastMessages).IsNotNull();
        await Assert.That(evaluator.LastMessages!.Count).IsEqualTo(1);
        await Assert.That(evaluator.LastMessages[0].Role).IsEqualTo(ChatRole.Assistant);
        await Assert.That(evaluator.LastResponse!.Messages[0].Text).IsEqualTo("standalone");
    }

    [Test]
    public async Task RunSessionAsync_UserOnlySession_LastResponseIsEmpty() {
        using var client = db.CreateClient();
        var sessionId    = Guid.NewGuid().ToString("N");

        await SeedSessionAsync(client, sessionId,
            EventFor(UserMsg("just a question", "m-1", 0)));

        var evaluator = new FixedSessionEvaluator(0.5, "Helpfulness");

        await new EvalRunner(client).RunSessionAsync(
            sessionId, "test-scorer", "test", evaluator);

        await Assert.That(evaluator.LastMessages).IsNotNull();
        await Assert.That(evaluator.LastMessages!.Count).IsEqualTo(1);
        await Assert.That(evaluator.LastMessages[0].Role).IsEqualTo(ChatRole.User);
        await Assert.That(evaluator.LastResponse!.Messages[0].Text).IsEqualTo("");
    }

    [Test]
    public async Task RunSessionAsync_FlattensToolCallsAndResults_PairsCallIds() {
        using var client = db.CreateClient();
        var sessionId    = Guid.NewGuid().ToString("N");

        var toolCalls = new AssistantToolCallsGenerated { MessageIndex = 1, Timestamp = Pts };
        toolCalls.ToolCalls.Add(new ToolCallInfo { CallId = "call-x", ToolName = "GetWeather" });

        await SeedSessionAsync(client, sessionId,
            EventFor(UserMsg("get the weather", "m-1", 0)),
            EventFor(toolCalls),
            EventFor(new ToolResultReceived {
                CallId       = "call-x",
                ToolName     = "GetWeather",
                Result       = "Sunny",
                MessageIndex = 2,
                Timestamp    = Pts,
            }),
            EventFor(AsstText("the weather is sunny", "m-4", 3)));

        var evaluator = new FixedSessionEvaluator(1.0, "Helpfulness");

        await new EvalRunner(client).RunSessionAsync(
            sessionId, "test-scorer", "test", evaluator);

        await Assert.That(evaluator.LastMessages).IsNotNull();
        await Assert.That(evaluator.LastMessages!.Count).IsEqualTo(4);

        var fcc = evaluator.LastMessages[1].Contents.OfType<FunctionCallContent>().Single();
        var frc = evaluator.LastMessages[2].Contents.OfType<FunctionResultContent>().Single();

        await Assert.That(fcc.CallId).IsEqualTo("call-x");
        await Assert.That(frc.CallId).IsEqualTo("call-x");
        await Assert.That(fcc.CallId).IsEqualTo(frc.CallId);
        await Assert.That(fcc.Name).IsEqualTo("GetWeather");
        await Assert.That(frc.Result).IsEqualTo("Sunny");

        await Assert.That(evaluator.LastMessages[3].Role).IsEqualTo(ChatRole.Assistant);
        await Assert.That(evaluator.LastMessages[3].Text).IsEqualTo("the weather is sunny");
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

    sealed class BooleanSessionEvaluator(bool value, string metricName) : IEvaluator {
        public IReadOnlyCollection<string> EvaluationMetricNames { get; } = [metricName];
        public ValueTask<EvaluationResult> EvaluateAsync(
                IEnumerable<ChatMessage> _, ChatResponse __, ChatConfiguration? ___ = null,
                IEnumerable<EvaluationContext>? ____ = null, CancellationToken _____ = default) =>
            ValueTask.FromResult(new EvaluationResult(new BooleanMetric(metricName, value)));
    }

    sealed class StringSessionEvaluator(string value, string metricName) : IEvaluator {
        public IReadOnlyCollection<string> EvaluationMetricNames { get; } = [metricName];
        public ValueTask<EvaluationResult> EvaluateAsync(
                IEnumerable<ChatMessage> _, ChatResponse __, ChatConfiguration? ___ = null,
                IEnumerable<EvaluationContext>? ____ = null, CancellationToken _____ = default) =>
            ValueTask.FromResult(new EvaluationResult(new StringMetric(metricName, value)));
    }

    sealed class NullNumericSessionEvaluator(string metricName) : IEvaluator {
        public IReadOnlyCollection<string> EvaluationMetricNames { get; } = [metricName];
        public ValueTask<EvaluationResult> EvaluateAsync(
                IEnumerable<ChatMessage> _, ChatResponse __, ChatConfiguration? ___ = null,
                IEnumerable<EvaluationContext>? ____ = null, CancellationToken _____ = default) =>
            ValueTask.FromResult(new EvaluationResult(new NumericMetric(metricName)));
    }

    sealed class ThrowingEvaluator(string metricName) : IEvaluator {
        public IReadOnlyCollection<string> EvaluationMetricNames { get; } = [metricName];
        public ValueTask<EvaluationResult> EvaluateAsync(
                IEnumerable<ChatMessage> _, ChatResponse __, ChatConfiguration? ___ = null,
                IEnumerable<EvaluationContext>? ____ = null, CancellationToken _____ = default) =>
            throw new InvalidOperationException("evaluator deliberately failed");
    }

    [Test]
    public async Task RunSessionAsync_OneEvaluatorThrows_OthersStillEmitMetrics() {
        using var client = db.CreateClient();
        var sessionId    = Guid.NewGuid().ToString("N");

        await SeedSessionAsync(client, sessionId,
            EventFor(UserMsg("q", "m-1", 0)),
            EventFor(AsstText("a", "m-2", 1)));

        var composite = new CompositeEvaluator(
            new FixedSessionEvaluator(0.5, "Good"),
            new ThrowingEvaluator("Bad"),
            new FixedSessionEvaluator(0.9, "AlsoGood"));

        // CompositeEvaluator absorbs the throw internally: it emits a metric for "Bad" with
        // InterpretationFailed=true (and an error diagnostic) rather than propagating the exception.
        // The run must complete and the two healthy evaluators must produce their metrics.
        var result = await new EvalRunner(client).RunSessionAsync(
            sessionId, "test-scorer", "test", composite);

        await Assert.That(result.ScoredMetrics.Count).IsEqualTo(3);
        var byName = result.ScoredMetrics.ToDictionary(s => s.MetricName);

        // Good and AlsoGood produce valid aggregable scores.
        await Assert.That(byName).ContainsKey("Good");
        await Assert.That(byName["Good"].Score).IsEqualTo(0.5);
        await Assert.That(byName["Good"].IsAggregable).IsTrue();

        await Assert.That(byName).ContainsKey("AlsoGood");
        await Assert.That(byName["AlsoGood"].Score).IsEqualTo(0.9);
        await Assert.That(byName["AlsoGood"].IsAggregable).IsTrue();

        // Bad is present but reflects the failure — CompositeEvaluator absorbs the throw and
        // emits a metric with an error diagnostic so the failure is visible without crashing the run.
        await Assert.That(byName).ContainsKey("Bad");
        await Assert.That(byName["Bad"].Diagnostics.Count).IsGreaterThan(0);
    }
}
