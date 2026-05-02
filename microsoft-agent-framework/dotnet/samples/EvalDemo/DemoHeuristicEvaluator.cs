using Microsoft.Extensions.AI;
using Microsoft.Extensions.AI.Evaluation;

namespace EvalDemo;

/// <summary>
/// Demo-only heuristic evaluator. No LLM call — pure inspection of the assistant message.
/// Tailored to the synthetic turns produced by this sample (the "weather"/"time" keyword check
/// is sample-specific). Real heuristic evaluators would be domain-tuned the same way.
/// </summary>
public sealed class DemoHeuristicEvaluator : IEvaluator {
    public const string MetricNameValue = "Helpfulness";

    public IReadOnlyCollection<string> EvaluationMetricNames { get; } = [MetricNameValue];

    public ValueTask<EvaluationResult> EvaluateAsync(
            IEnumerable<ChatMessage>            messages,
            ChatResponse                        modelResponse,
            ChatConfiguration?                  chatConfiguration = null,
            IEnumerable<EvaluationContext>?     additionalContext = null,
            CancellationToken                   cancellationToken = default
        ) {
        var assistant = modelResponse.Messages.FirstOrDefault(m => m.Role == ChatRole.Assistant);
        var output    = assistant?.Text ?? "";
        var userText  = messages.FirstOrDefault(m => m.Role == ChatRole.User)?.Text ?? "";

        var toolCalls    = assistant?.Contents.OfType<FunctionCallContent>().ToArray()         ?? [];
        var toolResults  = assistant?.Contents.OfType<FunctionResultContent>().ToArray()       ?? [];
        var toolErrors   = toolResults.Count(r => r.Exception is not null);

        var score   = 1.0;
        var reasons = new List<string>();

        if (string.IsNullOrWhiteSpace(output)) {
            score = 0.0;
            reasons.Add("empty response");
        } else if (output.Length < 10) {
            score -= 0.3;
            reasons.Add("very short response");
        }

        if (toolErrors > 0) {
            score -= 0.2 * toolErrors;
            reasons.Add($"{toolErrors} tool error(s)");
        }

        var needsTool = userText.Contains("weather", StringComparison.OrdinalIgnoreCase)
         || userText.Contains("time",                StringComparison.OrdinalIgnoreCase);

        if (needsTool && toolCalls.Length == 0) {
            score -= 0.3;
            reasons.Add("expected tool call but none made");
        }

        score = Math.Clamp(score, 0.0, 1.0);

        var (rating, failed) = score switch {
            >= 0.8 => (EvaluationRating.Good,     false),
            >= 0.5 => (EvaluationRating.Average,  false),
            _      => (EvaluationRating.Poor,     true),
        };

        var metric = new NumericMetric(MetricNameValue, score, reasons.Count == 0 ? null : string.Join("; ", reasons)) {
            Interpretation = new(rating, failed),
        };

        return ValueTask.FromResult(new EvaluationResult(metric));
    }
}
