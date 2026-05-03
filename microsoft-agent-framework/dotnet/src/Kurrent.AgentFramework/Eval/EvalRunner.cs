using System.Text.Json;
using Google.Protobuf;
using Google.Protobuf.WellKnownTypes;
using Kurrent.Agent.Schema;
using Kurrent.Agent.Schema.Events;
using Kurrent.AgentFramework.Serialization;
using KurrentDB.Client;
using Microsoft.Extensions.AI;
using Microsoft.Extensions.AI.Evaluation;

namespace Kurrent.AgentFramework.Eval;

/// <summary>
/// One row of the eval output: a single <see cref="EvaluationMetric"/>, optionally
/// scoped to a single <see cref="Turn"/>. <see cref="Turn"/> is non-null when the
/// row came from <see cref="EvalRunner.RunAsync"/> (per-turn) and null when it
/// came from <see cref="EvalRunner.RunSessionAsync"/> (session-level).
/// </summary>
/// <param name="Score">
/// The numeric value persisted on <c>TurnScored.score</c>. <c>0</c> when the metric has no numeric
/// meaning (<see cref="StringMetric"/>, or <see cref="NumericMetric"/> with a null value); see
/// <paramref name="IsAggregable"/>.
/// </param>
/// <param name="IsAggregable">
/// <c>true</c> when <paramref name="Score"/> carries genuine numeric meaning and should participate
/// in per-metric averaging. <c>false</c> for string-valued or missing-value metrics, whose qualitative
/// content is preserved under <c>extensions.afw.eval</c> instead.
/// </param>
public sealed record ScoredMetric(
        Turn?                 Turn,
        string                MetricName,
        double                Score,
        string                MetricKind,
        bool                  IsAggregable,
        string?               StringValue,
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

            // Batch all per-turn TurnScored events into one append. With multiple metrics per
            // turn, per-metric appends would multiply KurrentDB round-trips by N.
            var turnEvents = new List<EventData>(evalResult.Metrics.Count);

            foreach (var metric in evalResult.Metrics.Values) {
                var scored = ToScoredMetric(turn, metric);
                scoredMetrics.Add(scored);

                if (scored.IsAggregable) {
                    var prev = sums.GetValueOrDefault(metric.Name);
                    sums[metric.Name] = (prev.Sum + scored.Score, prev.Count + 1);
                }

                turnEvents.Add(EventSerializer.Serialize(BuildTurnScored(sessionId, scored)));
            }

            if (turnEvents.Count > 0)
                await client.AppendToStreamAsync(stream, StreamState.Any, turnEvents, cancellationToken: ct)
                    .ConfigureAwait(false);
        }

        var perMetricAverage = sums.ToDictionary(kv => kv.Key, kv => kv.Value.Sum / kv.Value.Count);

        // Cross-metric averaging is meaningless (see EvalResult docs), so AverageScore only
        // carries the single-metric run's average; multi-metric runs leave it at 0 and rely on
        // extensions.afw.eval.per_metric_average for the meaningful aggregation.
        var completed = new EvalRunCompleted {
            SessionId    = sessionId,
            TurnsScored  = turns.Count,
            AverageScore = perMetricAverage.Count == 1 ? perMetricAverage.Values.Single() : 0,
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

    /// <summary>
    /// Run an evaluation against a session as a single unit. The entire conversation is flattened
    /// into one <c>IList&lt;ChatMessage&gt;</c> and passed to <paramref name="evaluator"/> exactly
    /// once. Emits one <see cref="SessionScored"/> per metric returned, not per turn.
    /// </summary>
    /// <param name="sessionId">Session id whose stream provides the conversation.</param>
    /// <param name="scorerName">Free-text scorer identifier persisted on <see cref="EvalRunStarted"/>.</param>
    /// <param name="criteria">Free-text criteria description persisted on <see cref="EvalRunStarted"/>.</param>
    /// <param name="evaluator">The evaluator to apply to the whole session. Wrap multiple evaluators in a <see cref="CompositeEvaluator"/>.</param>
    /// <param name="chatConfiguration">Required when the evaluator uses an <see cref="IChatClient"/>; <c>null</c> for purely heuristic evaluators.</param>
    /// <param name="additionalContext">Optional context for the evaluator (e.g. for <see cref="GroundednessEvaluator"/>).</param>
    public async Task<EvalResult> RunSessionAsync(
            string                          sessionId,
            string                          scorerName,
            string                          criteria,
            IEvaluator                      evaluator,
            ChatConfiguration?              chatConfiguration = null,
            IEnumerable<EvaluationContext>? additionalContext = null,
            CancellationToken               ct                = default
        ) {
        var (messages, response, inputTokens, outputTokens) =
            await FlattenSessionAsync(client, sessionId, ct).ConfigureAwait(false);

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

        if (messages.Count > 0) {
            var evalResult = await evaluator
                .EvaluateAsync(messages, response, chatConfiguration, additionalContext, ct)
                .ConfigureAwait(false);

            var sessionEvents = new List<EventData>(evalResult.Metrics.Count);

            foreach (var metric in evalResult.Metrics.Values) {
                var scored = ToScoredMetric(turn: null, metric);
                scoredMetrics.Add(scored);

                if (scored.IsAggregable) {
                    var prev = sums.GetValueOrDefault(metric.Name);
                    sums[metric.Name] = (prev.Sum + scored.Score, prev.Count + 1);
                }

                sessionEvents.Add(EventSerializer.Serialize(BuildSessionScored(sessionId, scored)));
            }

            if (sessionEvents.Count > 0)
                await client.AppendToStreamAsync(stream, StreamState.Any, sessionEvents, cancellationToken: ct)
                    .ConfigureAwait(false);
        }

        var perMetricAverage = sums.ToDictionary(kv => kv.Key, kv => kv.Value.Sum / kv.Value.Count);

        var completed = new EvalRunCompleted {
            SessionId    = sessionId,
            TurnsScored  = 0, // session-level run; turn count not meaningful here
            AverageScore = perMetricAverage.Count == 1 ? perMetricAverage.Values.Single() : 0,
            Timestamp    = Timestamp.FromDateTimeOffset(DateTimeOffset.UtcNow),
        };
        if (perMetricAverage.Count > 0) completed.Extensions["afw"] = BuildCompletedExtension(perMetricAverage);
        await AppendAsync(stream, completed, ct).ConfigureAwait(false);

        return new(sessionId, scoredMetrics, perMetricAverage, inputTokens, outputTokens);
    }

    static async Task<(IList<ChatMessage> Messages, ChatResponse Response, long? InputTokens, long? OutputTokens)>
        FlattenSessionAsync(KurrentDBClient client, string sessionId, CancellationToken ct) {
        var streamName = StreamNames.AgentSession(sessionId);
        var messages   = new List<ChatMessage>();
        long? inputTokens  = null;
        long? outputTokens = null;
        ChatMessage? lastAssistant = null;

        try {
            var events = client.ReadStreamAsync(Direction.Forwards, streamName, StreamPosition.Start, cancellationToken: ct);
            await foreach (var resolved in events.ConfigureAwait(false)) {
                var domainEvent = EventSerializer.Deserialize(resolved);
                if (domainEvent is null) continue;

                switch (domainEvent) {
                    case UserMessageReceived userMsg:
                        if (userMsg.HasContent)
                            messages.Add(new ChatMessage(ChatRole.User, userMsg.Content));
                        break;

                    case AssistantTextGenerated asstMsg:
                        if (asstMsg.HasContent) {
                            var msg = new ChatMessage(ChatRole.Assistant, asstMsg.Content);
                            messages.Add(msg);
                            lastAssistant = msg;
                        } else {
                            // A content-less AssistantTextGenerated signals turn-end without a final
                            // text response. Clear lastAssistant so the response falls through to the
                            // empty default rather than wrapping a prior tool-call message.
                            lastAssistant = null;
                        }
                        ReadUsageFromMetadata(resolved, ref inputTokens, ref outputTokens);
                        break;

                    case AssistantToolCallsGenerated toolCalls:
                        var toolMsg = new ChatMessage(ChatRole.Assistant, toolCalls.HasContent ? toolCalls.Content : null);
                        for (var i = 0; i < toolCalls.ToolCalls.Count; i++) {
                            var tc     = toolCalls.ToolCalls[i];
                            var callId = string.IsNullOrEmpty(tc.CallId) ? $"call-{i}" : tc.CallId;
                            toolMsg.Contents.Add(new FunctionCallContent(
                                callId:    callId,
                                name:      tc.ToolName,
                                arguments: ParseToolArguments(StructToJson(tc.Arguments))));
                        }
                        messages.Add(toolMsg);
                        lastAssistant = toolMsg;
                        ReadUsageFromMetadata(resolved, ref inputTokens, ref outputTokens);
                        break;

                    case ToolResultReceived toolResult:
                        if (toolResult.HasResult) {
                            messages.Add(new ChatMessage(ChatRole.Tool, [
                                new FunctionResultContent(
                                    callId: toolResult.CallId,
                                    result: toolResult.Result)
                            ]));
                        }
                        break;
                }
            }
        } catch (StreamNotFoundException) { }

        var response = lastAssistant is null
            ? new ChatResponse(new ChatMessage(ChatRole.Assistant, ""))
            : new ChatResponse(lastAssistant);

        return (messages, response, inputTokens, outputTokens);
    }

    static string? StructToJson(Struct? args) =>
        args is null || args.Fields.Count == 0
            ? null
            : JsonFormatter.Default.Format(args);

    static void ReadUsageFromMetadata(ResolvedEvent resolved, ref long? inputTokens, ref long? outputTokens) {
        if (resolved.Event.Metadata.Length == 0) return;
        try {
            var meta = JsonSerializer.Deserialize<JsonElement>(resolved.Event.Metadata.Span);
            if (!meta.TryGetProperty("$usage", out var usage)) return;

            if (usage.TryGetProperty("input_tokens", out var inp) && inp.ValueKind == JsonValueKind.Number)
                inputTokens = (inputTokens ?? 0) + inp.GetInt64();

            if (usage.TryGetProperty("output_tokens", out var outp) && outp.ValueKind == JsonValueKind.Number)
                outputTokens = (outputTokens ?? 0) + outp.GetInt64();
        } catch { }
    }

    static (IList<ChatMessage> Messages, ChatResponse Response) ToChat(Turn turn) {
        var messages = new List<ChatMessage> { new(ChatRole.User, turn.UserInput ?? "") };

        var assistant = new ChatMessage(ChatRole.Assistant, turn.AssistantOutput ?? "");

        // Turn.ToolCall has no upstream call id, so synthesize a per-turn unique one. This
        // keeps FunctionCallContent / FunctionResultContent paired correctly when the same
        // tool is invoked more than once in a single turn.
        for (var i = 0; i < turn.ToolCalls.Count; i++) {
            var tc     = turn.ToolCalls[i];
            var callId = $"call-{i}";

            assistant.Contents.Add(new FunctionCallContent(
                callId:    callId,
                name:      tc.Name,
                arguments: ParseToolArguments(tc.Arguments)));

            if (tc.Result is not null)
                assistant.Contents.Add(new FunctionResultContent(callId: callId, result: tc.Result) {
                    Exception = tc.IsError ? new InvalidOperationException(tc.Result) : null,
                });
        }

        return (messages, new ChatResponse(assistant));
    }

    static IDictionary<string, object?>? ParseToolArguments(string? json) {
        if (string.IsNullOrWhiteSpace(json)) return null;
        try {
            return JsonSerializer.Deserialize<Dictionary<string, object?>>(json);
        } catch (JsonException) {
            return null;
        }
    }

    static ScoredMetric ToScoredMetric(Turn? turn, EvaluationMetric metric) {
        var (score, kind, isAggregable, stringValue) = metric switch {
            NumericMetric { Value: { } v } => (v,                          "numeric", true,  (string?)null),
            NumericMetric                  => (0d,                         "numeric", false, (string?)null),
            BooleanMetric b                => (b.Value == true ? 1d : 0d,  "boolean", true,  (string?)null),
            StringMetric s                 => (0d,                         "string",  false, s.Value),
            _                              => (0d,                         "none",    false, (string?)null),
        };

        var diagnostics = metric.Diagnostics is null
            ? Array.Empty<string>()
            : metric.Diagnostics.Select(d => $"[{d.Severity}] {d.Message}").ToArray();

        return new(
            turn,
            metric.Name,
            score,
            kind,
            isAggregable,
            stringValue,
            metric.Reason,
            metric.Interpretation?.Rating.ToString(),
            metric.Interpretation?.Failed ?? false,
            diagnostics
        );
    }

    static TurnScored BuildTurnScored(string sessionId, ScoredMetric scored) {
        // BuildTurnScored is only reachable from RunAsync, which always passes a non-null Turn.
        // Extracting the local once both proves the assertion and avoids repeating ! on every read.
        var turn = scored.Turn!;
        var evt  = new TurnScored {
            SessionId  = sessionId,
            TurnIndex  = turn.Index,
            Score      = scored.Score,
            ScoreLabel = scored.MetricName,
            Timestamp  = Timestamp.FromDateTimeOffset(DateTimeOffset.UtcNow),
        };
        if (turn.UserInput       is not null) evt.Input  = turn.UserInput;
        if (turn.AssistantOutput is not null) evt.Output = turn.AssistantOutput;
        if (scored.Reason        is not null) evt.Reason = scored.Reason;

        evt.Extensions["afw"] = BuildScoredExtension(scored);

        return evt;
    }

    static SessionScored BuildSessionScored(string sessionId, ScoredMetric scored) {
        var evt = new SessionScored {
            SessionId  = sessionId,
            Score      = scored.Score,
            ScoreLabel = scored.MetricName,
            Timestamp  = Timestamp.FromDateTimeOffset(DateTimeOffset.UtcNow),
        };
        if (scored.Reason is not null) evt.Reason = scored.Reason;

        evt.Extensions["afw"] = BuildScoredExtension(scored);

        return evt;
    }

    static Struct BuildScoredExtension(ScoredMetric scored) {
        var eval = new Struct();
        eval.Fields["metric_kind"] = Value.ForString(scored.MetricKind);
        eval.Fields["is_aggregable"] = Value.ForBool(scored.IsAggregable);

        if (scored.StringValue is not null)
            eval.Fields["string_value"] = Value.ForString(scored.StringValue);

        // NumericMetric with no value reaches the event stream as score=0 with this flag set,
        // so readers can tell "evaluator ran, produced no number" apart from a real zero.
        if (scored is { MetricKind: "numeric", IsAggregable: false })
            eval.Fields["value_missing"] = Value.ForBool(true);

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
