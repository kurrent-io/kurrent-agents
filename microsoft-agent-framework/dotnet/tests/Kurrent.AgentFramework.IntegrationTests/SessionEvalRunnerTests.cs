using System.Text.Json;
using Kurrent.AgentFramework.Eval;
using KurrentDB.Client;
using Microsoft.Extensions.AI;
using Microsoft.Extensions.AI.Evaluation;

namespace Kurrent.AgentFramework.IntegrationTests;

[ClassDataSource<KurrentDbFixture>(Shared = SharedType.PerTestSession)]
public class SessionEvalRunnerTests(KurrentDbFixture db) {
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
