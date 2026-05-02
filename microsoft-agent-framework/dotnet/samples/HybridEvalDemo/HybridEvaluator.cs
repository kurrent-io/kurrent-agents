using Microsoft.Extensions.AI;
using Microsoft.Extensions.AI.Evaluation;

namespace HybridEvalDemo;

/// <summary>
/// Composes a cheap heuristic evaluator with one or more LLM-based evaluators.
/// The heuristic always runs; LLM evaluators only run when the heuristic's score
/// lands in an ambiguous mid-band (between 0.15 and 0.85), saving LLM calls on
/// confident pass/fail cases.
///
/// Sample-only — production users would compose evaluators with
/// <see cref="CompositeEvaluator"/> or their own escalation policy.
/// </summary>
public sealed class HybridEvaluator(
        IEvaluator              heuristic,
        Action                  onEscalation,
        Action                  onConfident,
        params IEvaluator[]     escalationEvaluators
    ) : IEvaluator {
    public IReadOnlyCollection<string> EvaluationMetricNames { get; } = heuristic.EvaluationMetricNames
        .Concat(escalationEvaluators.SelectMany(e => e.EvaluationMetricNames))
        .Distinct()
        .ToArray();

    public async ValueTask<EvaluationResult> EvaluateAsync(
            IEnumerable<ChatMessage>            messages,
            ChatResponse                        modelResponse,
            ChatConfiguration?                  chatConfiguration = null,
            IEnumerable<EvaluationContext>?     additionalContext = null,
            CancellationToken                   cancellationToken = default
        ) {
        // Materialize once — additionalContext / messages may be enumerated more than once.
        var messageList = messages as IList<ChatMessage> ?? [.. messages];
        var contextList = additionalContext?.ToArray();

        var heuristicResult = await heuristic
            .EvaluateAsync(messageList, modelResponse, chatConfiguration, contextList, cancellationToken)
            .ConfigureAwait(false);

        var heuristicScore = heuristicResult.Metrics.Values.OfType<NumericMetric>()
            .Select(m => m.Value ?? 0)
            .DefaultIfEmpty(0.5)
            .First();

        if (heuristicScore is >= 0.85 or <= 0.15) {
            onConfident();
            return heuristicResult;
        }

        onEscalation();

        var combined = new Dictionary<string, EvaluationMetric>(heuristicResult.Metrics);

        foreach (var ev in escalationEvaluators) {
            var r = await ev.EvaluateAsync(messageList, modelResponse, chatConfiguration, contextList, cancellationToken)
                .ConfigureAwait(false);
            foreach (var (name, metric) in r.Metrics) combined[name] = metric;
        }

        return new EvaluationResult(combined);
    }
}
