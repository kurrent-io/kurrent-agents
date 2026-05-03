using Microsoft.Extensions.AI;
using Microsoft.Extensions.AI.Evaluation;

namespace SessionEvalDemo;

/// <summary>
/// Demo-only session-level evaluator. No LLM call. Inspects the entire flattened
/// conversation and emits two metrics observable from the canonical schema:
/// <list type="bullet">
///   <item><description>
///     <c>ToolUsageRatio</c> — tool calls per user message. Always observable from
///     <see cref="FunctionCallContent"/> on assistant messages and the count of
///     <see cref="ChatRole.User"/> messages.
///   </description></item>
///   <item><description>
///     <c>OverallVerdict</c> — string summary derived from message counts and
///     assistant text length.
///   </description></item>
/// </list>
/// <para>
/// Note: the canonical <c>ToolResultReceived</c> event has no error flag and the
/// session flatten reconstructs <see cref="FunctionResultContent"/> without
/// <see cref="FunctionResultContent.Exception"/>, so a "tool error ratio" metric
/// would always read 0 against current schema data. <c>ToolUsageRatio</c> is the
/// observable substitute.
/// </para>
/// </summary>
public sealed class DemoSessionEvaluator : IEvaluator {
    public const string ToolUsageRatioMetric = "ToolUsageRatio";
    public const string OverallVerdictMetric = "OverallVerdict";

    public IReadOnlyCollection<string> EvaluationMetricNames { get; } =
        [ToolUsageRatioMetric, OverallVerdictMetric];

    public ValueTask<EvaluationResult> EvaluateAsync(
            IEnumerable<ChatMessage>        messages,
            ChatResponse                    modelResponse,
            ChatConfiguration?              chatConfiguration = null,
            IEnumerable<EvaluationContext>? additionalContext = null,
            CancellationToken               cancellationToken = default
        ) {
        var msgList = messages.ToList();
        var assistantMessages = msgList.Where(m => m.Role == ChatRole.Assistant).ToList();
        var userMessageCount  = msgList.Count(m => m.Role == ChatRole.User);

        var toolCalls = msgList
            .SelectMany(m => m.Contents.OfType<FunctionCallContent>())
            .Count();

        var toolUsageRatio = userMessageCount == 0
            ? 0d
            : (double)toolCalls / userMessageCount;

        // Defensive null-coalesce on Text — assistant tool-call messages with no
        // accompanying text have no TextContent items in this build of MEAI;
        // future versions could surface a null Text instead of "".
        var assistantTextLength = assistantMessages.Sum(m => (m.Text ?? "").Length);

        string verdict;
        if (assistantMessages.Count == 0)              verdict = "stalled";
        else if (toolCalls == 0 && userMessageCount > 0) verdict = "no tools used";
        else if (assistantTextLength < 50)             verdict = "thin output";
        else                                           verdict = "looked productive";

        var ratio = new NumericMetric(ToolUsageRatioMetric, toolUsageRatio,
            $"{toolCalls} tool call(s) across {userMessageCount} user message(s)") {
            Interpretation = new(toolUsageRatio switch {
                0d            => EvaluationRating.Average,  // no tools — context-dependent
                <= 3d         => EvaluationRating.Good,
                _             => EvaluationRating.Poor      // > 3 tools per turn — likely thrashing
            }, failed: false),
        };

        var summary = new StringMetric(OverallVerdictMetric, verdict);

        return ValueTask.FromResult(new EvaluationResult(ratio, summary));
    }
}
