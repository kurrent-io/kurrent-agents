using Microsoft.Extensions.AI;
using Microsoft.Extensions.AI.Evaluation;

namespace SessionEvalDemo;

/// <summary>
/// Demo-only session-level evaluator. No LLM call. Inspects the entire flattened
/// conversation and emits two metrics: a numeric "ToolErrorRatio" (fraction of
/// FunctionResultContent items that carry an Exception) and a string "OverallVerdict"
/// summarising the run.
/// </summary>
public sealed class DemoSessionEvaluator : IEvaluator {
    public const string ToolErrorRatioMetric = "ToolErrorRatio";
    public const string OverallVerdictMetric = "OverallVerdict";

    public IReadOnlyCollection<string> EvaluationMetricNames { get; } =
        [ToolErrorRatioMetric, OverallVerdictMetric];

    public ValueTask<EvaluationResult> EvaluateAsync(
            IEnumerable<ChatMessage>        messages,
            ChatResponse                    modelResponse,
            ChatConfiguration?              chatConfiguration = null,
            IEnumerable<EvaluationContext>? additionalContext = null,
            CancellationToken               cancellationToken = default
        ) {
        var msgList   = messages.ToList();
        var assistantOutputs = msgList
            .Where(m => m.Role == ChatRole.Assistant)
            .ToList();

        var toolResults = msgList
            .SelectMany(m => m.Contents.OfType<FunctionResultContent>())
            .ToList();

        var toolErrors = toolResults.Count(r => r.Exception is not null);
        var toolRatio  = toolResults.Count == 0 ? 0d : (double)toolErrors / toolResults.Count;

        string verdict;
        if (assistantOutputs.Count == 0)               verdict = "stalled";
        else if (toolRatio > 0.25)                     verdict = "tool-heavy with errors";
        else if (assistantOutputs.Sum(m => m.Text.Length) < 50) verdict = "thin output";
        else                                           verdict = "looked productive";

        var ratio = new NumericMetric(ToolErrorRatioMetric, toolRatio,
            $"{toolErrors}/{toolResults.Count} tool calls returned an error") {
            Interpretation = new(toolRatio switch {
                <= 0.05 => EvaluationRating.Good,
                <= 0.25 => EvaluationRating.Average,
                _       => EvaluationRating.Poor
            }, failed: toolRatio > 0.25),
        };

        var summary = new StringMetric(OverallVerdictMetric, verdict);

        return ValueTask.FromResult(new EvaluationResult(ratio, summary));
    }
}
