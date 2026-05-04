using Microsoft.Extensions.AI;
using Microsoft.Extensions.AI.Evaluation;

namespace HybridEvalDemo;

/// <summary>
/// Demo-only heuristic evaluator. Returns extreme scores when confident (empty / clear pass)
/// and mid-band scores when uncertain — the <see cref="HybridEvaluator"/> uses the score band
/// to decide whether to escalate to an LLM-based evaluator.
/// </summary>
public sealed class DemoHeuristicEvaluator : IEvaluator {
    public const string MetricNameValue = "Helpfulness";

    public IReadOnlyCollection<string> EvaluationMetricNames { get; } = [MetricNameValue];

    public ValueTask<EvaluationResult> EvaluateAsync(
            IEnumerable<ChatMessage>        messages,
            ChatResponse                    modelResponse,
            ChatConfiguration?              chatConfiguration = null,
            IEnumerable<EvaluationContext>? additionalContext = null,
            CancellationToken               cancellationToken = default
        ) {
        var assistant = modelResponse.Messages.FirstOrDefault(m => m.Role == ChatRole.Assistant);
        var output    = assistant?.Text                                             ?? "";
        var userText  = messages.FirstOrDefault(m => m.Role == ChatRole.User)?.Text ?? "";

        var toolCalls   = assistant?.Contents.OfType<FunctionCallContent>().ToArray()   ?? [];
        var toolResults = assistant?.Contents.OfType<FunctionResultContent>().ToArray() ?? [];
        var toolErrors  = toolResults.Count(r => r.Exception is not null);

        if (string.IsNullOrWhiteSpace(output)) {
            return Done(0.0, EvaluationRating.Poor, true, "empty response");
        }

        var reasons = new List<string>();
        var score   = 1.0;

        switch (output.Length) {
            case < 10:
                score = 0.5;
                reasons.Add("very short response");

                break;
            case < 40:
                score = 0.6;
                reasons.Add("short response");

                break;
        }

        if (toolErrors > 0) {
            score -= 0.2 * toolErrors;
            reasons.Add($"{toolErrors} tool error(s)");
        }

        var needsTool = userText.Contains("weather", StringComparison.OrdinalIgnoreCase)
         || userText.Contains("time", StringComparison.OrdinalIgnoreCase);

        if (needsTool && toolCalls.Length == 0) {
            score = Math.Min(score, 0.55);
            reasons.Add("expected tool call but none made");
        }

        score = Math.Clamp(score, 0.0, 1.0);

        var (rating, failed) = score switch {
            >= 0.85 => (EvaluationRating.Good, false),
            >= 0.5  => (EvaluationRating.Average, false),
            _       => (EvaluationRating.Poor, true),
        };

        return Done(score, rating, failed, reasons.Count == 0 ? null : string.Join("; ", reasons));
    }

    static ValueTask<EvaluationResult> Done(double score, EvaluationRating rating, bool failed, string? reason) {
        var metric = new NumericMetric(MetricNameValue, score, reason) {
            Interpretation = new(rating, failed),
        };

        return ValueTask.FromResult(new EvaluationResult(metric));
    }
}
