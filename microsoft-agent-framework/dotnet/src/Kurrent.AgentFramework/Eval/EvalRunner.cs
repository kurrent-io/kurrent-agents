using Google.Protobuf.WellKnownTypes;
using Kurrent.Agent.Schema;
using Kurrent.Agent.Schema.Events;
using Kurrent.AgentFramework.Serialization;
using KurrentDB.Client;
using Microsoft.Extensions.AI;
using Microsoft.Extensions.AI.Evaluation;

namespace Kurrent.AgentFramework.Eval;

/// <summary>
/// One row of the eval output: a single <see cref="EvaluationMetric"/> against a single <see cref="Turn"/>.
/// </summary>
public sealed record ScoredMetric(
        Turn                  Turn,
        string                MetricName,
        double                Score,
        string                MetricKind,
        string?               Reason,
        string?               InterpretationRating,
        bool                  InterpretationFailed,
        IReadOnlyList<string> Diagnostics
    );

/// <summary>
/// Result of an eval run across a session. Per-turn scores are not aggregated across metrics —
/// averaging heterogeneous metrics produces a number with no meaning. Per-metric averages are
/// the meaningful aggregation.
/// </summary>
public sealed record EvalResult(
        string                              SessionId,
        IReadOnlyList<ScoredMetric>         ScoredMetrics,
        IReadOnlyDictionary<string, double> PerMetricAverage,
        long?                               TotalInputTokens,
        long?                               TotalOutputTokens
    );

/// <summary>
/// Runs evaluations against agent sessions stored in KurrentDB.
/// Reads turns from the session stream, evaluates each turn with an <see cref="IEvaluator"/>
/// (or <see cref="CompositeEvaluator"/>) from <c>Microsoft.Extensions.AI.Evaluation</c>, and
/// writes one <see cref="TurnScored"/> per (turn, metric) to a dedicated <c>EvalRun-{id}</c>
/// stream alongside <see cref="EvalRunStarted"/> / <see cref="EvalRunCompleted"/>.
/// </summary>
public sealed class EvalRunner(KurrentDBClient client) {
    /// <summary>
    /// Run an evaluation against a session.
    /// </summary>
    /// <param name="sessionId">Session id whose stream provides the turns to evaluate.</param>
    /// <param name="scorerName">Free-text scorer identifier persisted on <see cref="EvalRunStarted"/>.</param>
    /// <param name="criteria">Free-text criteria description persisted on <see cref="EvalRunStarted"/>.</param>
    /// <param name="evaluator">The evaluator to apply per turn. Wrap multiple evaluators in a <see cref="CompositeEvaluator"/>.</param>
    /// <param name="chatConfiguration">Required when the evaluator uses an <see cref="IChatClient"/>; <c>null</c> for purely heuristic evaluators.</param>
    /// <param name="additionalContext">Optional per-turn context supplier (e.g. for <see cref="GroundednessEvaluator"/>).</param>
    public async Task<EvalResult> RunAsync(
            string                                       sessionId,
            string                                       scorerName,
            string                                       criteria,
            IEvaluator                                   evaluator,
            ChatConfiguration?                           chatConfiguration = null,
            Func<Turn, IEnumerable<EvaluationContext>?>? additionalContext = null,
            CancellationToken                            ct                = default
        ) {
        var turns  = await SessionTurnReader.ReadTurnsAsync(client, sessionId, ct).ConfigureAwait(false);
        var evalId = Guid.NewGuid().ToString("N");
        var stream = StreamNames.EvalRun(evalId);
        var now    = DateTimeOffset.UtcNow;

        await AppendAsync(stream, new EvalRunStarted {
            SessionId = sessionId,
            Scorer    = scorerName,
            Criteria  = criteria,
            Timestamp = Timestamp.FromDateTimeOffset(now),
        }, ct).ConfigureAwait(false);

        var scoredMetrics = new List<ScoredMetric>();
        var sums          = new Dictionary<string, (double Sum, int Count)>();

        foreach (var turn in turns) {
            var (messages, response) = ToChat(turn);
            var context              = additionalContext?.Invoke(turn);

            var evalResult = await evaluator
                .EvaluateAsync(messages, response, chatConfiguration, context, ct)
                .ConfigureAwait(false);

            foreach (var metric in evalResult.Metrics.Values) {
                var scored = ToScoredMetric(turn, metric);
                scoredMetrics.Add(scored);

                var prev = sums.GetValueOrDefault(metric.Name);
                sums[metric.Name] = (prev.Sum + scored.Score, prev.Count + 1);

                await AppendAsync(stream, BuildTurnScored(sessionId, scored), ct).ConfigureAwait(false);
            }
        }

        var perMetricAverage = sums.ToDictionary(kv => kv.Key, kv => kv.Value.Sum / kv.Value.Count);

        var completed = new EvalRunCompleted {
            SessionId    = sessionId,
            TurnsScored  = turns.Count,
            AverageScore = perMetricAverage.Values.Count > 0 ? perMetricAverage.Values.Average() : 0,
            Timestamp    = Timestamp.FromDateTimeOffset(DateTimeOffset.UtcNow),
        };
        if (perMetricAverage.Count > 0) completed.Extensions["afw"] = BuildCompletedExtension(perMetricAverage);
        await AppendAsync(stream, completed, ct).ConfigureAwait(false);

        return new(
            sessionId,
            scoredMetrics,
            perMetricAverage,
            turns.Where(t => t.InputTokens.HasValue).Sum(t => t.InputTokens!.Value),
            turns.Where(t => t.OutputTokens.HasValue).Sum(t => t.OutputTokens!.Value)
        );
    }

    static (IList<ChatMessage> Messages, ChatResponse Response) ToChat(Turn turn) {
        var messages = new List<ChatMessage> { new(ChatRole.User, turn.UserInput ?? "") };

        var assistant = new ChatMessage(ChatRole.Assistant, turn.AssistantOutput ?? "");
        foreach (var tc in turn.ToolCalls) {
            assistant.Contents.Add(new FunctionCallContent(callId: tc.Name, name: tc.Name));
            if (tc.Result is not null)
                assistant.Contents.Add(new FunctionResultContent(callId: tc.Name, result: tc.Result) {
                    Exception = tc.IsError ? new InvalidOperationException(tc.Result) : null,
                });
        }

        return (messages, new ChatResponse(assistant));
    }

    static ScoredMetric ToScoredMetric(Turn turn, EvaluationMetric metric) {
        var (score, kind) = metric switch {
            NumericMetric n => (n.Value ?? 0d, "numeric"),
            BooleanMetric b => (b.Value == true ? 1d : 0d, "boolean"),
            StringMetric _  => (0d, "string"),
            _               => (0d, "none"),
        };

        var diagnostics = metric.Diagnostics is null
            ? Array.Empty<string>()
            : metric.Diagnostics.Select(d => $"[{d.Severity}] {d.Message}").ToArray();

        return new(
            turn,
            metric.Name,
            score,
            kind,
            metric.Reason,
            metric.Interpretation?.Rating.ToString(),
            metric.Interpretation?.Failed ?? false,
            diagnostics
        );
    }

    static TurnScored BuildTurnScored(string sessionId, ScoredMetric scored) {
        var evt = new TurnScored {
            SessionId  = sessionId,
            TurnIndex  = scored.Turn.Index,
            Score      = scored.Score,
            ScoreLabel = scored.MetricName,
            Timestamp  = Timestamp.FromDateTimeOffset(DateTimeOffset.UtcNow),
        };
        if (scored.Turn.UserInput       is not null) evt.Input  = scored.Turn.UserInput;
        if (scored.Turn.AssistantOutput is not null) evt.Output = scored.Turn.AssistantOutput;
        if (scored.Reason               is not null) evt.Reason = scored.Reason;

        evt.Extensions["afw"] = BuildScoredExtension(scored);

        return evt;
    }

    static Struct BuildScoredExtension(ScoredMetric scored) {
        var eval = new Struct();
        eval.Fields["metric_kind"] = Value.ForString(scored.MetricKind);

        if (scored.InterpretationRating is not null) {
            var interp = new Struct();
            interp.Fields["rating"] = Value.ForString(scored.InterpretationRating);
            interp.Fields["failed"] = Value.ForBool(scored.InterpretationFailed);
            eval.Fields["interpretation"] = Value.ForStruct(interp);
        }

        if (scored.Diagnostics.Count > 0) {
            eval.Fields["diagnostics"] = Value.ForList(
                scored.Diagnostics.Select(d => Value.ForString(d)).ToArray()
            );
        }

        var afw = new Struct();
        afw.Fields["eval"] = Value.ForStruct(eval);

        return afw;
    }

    static Struct BuildCompletedExtension(IReadOnlyDictionary<string, double> perMetricAverage) {
        var averages = new Struct();
        foreach (var (name, value) in perMetricAverage) averages.Fields[name] = Value.ForNumber(value);

        var eval = new Struct();
        eval.Fields["per_metric_average"] = Value.ForStruct(averages);

        var afw = new Struct();
        afw.Fields["eval"] = Value.ForStruct(eval);

        return afw;
    }

    async Task AppendAsync(string stream, object @event, CancellationToken ct) =>
        await client.AppendToStreamAsync(
                stream,
                StreamState.Any,
                [EventSerializer.Serialize(@event)],
                cancellationToken: ct
            )
            .ConfigureAwait(false);
}
