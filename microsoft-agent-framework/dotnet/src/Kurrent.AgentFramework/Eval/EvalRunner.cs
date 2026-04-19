using System.Text.Json;
using System.Text.Json.Serialization;
using Kurrent.AgentFramework.Events;
using KurrentDB.Client;
using Microsoft.Extensions.AI;

namespace Kurrent.AgentFramework.Eval;

/// <summary>
/// Scored result for a single turn.
/// </summary>
public sealed record ScoredTurn(Turn Turn, double Score, string? Label, string? Reason);

/// <summary>
/// Result of an eval run across an entire session.
/// </summary>
public sealed record EvalResult(
        string                    SessionId,
        IReadOnlyList<ScoredTurn> ScoredTurns,
        double                    AverageScore,
        long?                     TotalInputTokens,
        long?                     TotalOutputTokens
    );

/// <summary>
/// Runs evaluations against agent sessions stored in KurrentDB.
/// Reads turns from the session stream, scores each turn with a provided scorer,
/// and writes score events back to a dedicated eval stream.
///
/// Scorers can be:
/// - LLM-as-judge (pass an IChatClient)
/// - Heuristic functions
/// - Any async function that takes a Turn and returns a score
/// </summary>
public sealed class EvalRunner(KurrentDBClient client) {
    static readonly JsonSerializerOptions JsonOptions = new() {
        PropertyNamingPolicy   = JsonNamingPolicy.SnakeCaseLower,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
    };

    /// <summary>
    /// Run an evaluation against a session using a custom scoring function.
    /// Writes EvalRunStarted, TurnScored, and EvalRunCompleted events to an EvalRun-{id} stream.
    /// </summary>
    public async Task<EvalResult> RunAsync(
            string                                          sessionId,
            string                                          scorerName,
            string                                          criteria,
            Func<Turn, CancellationToken, Task<ScoredTurn>> scorer,
            CancellationToken                               ct = default
        ) {
        var turns  = await SessionTurnReader.ReadTurnsAsync(client, sessionId, ct).ConfigureAwait(false);
        var evalId = Guid.NewGuid().ToString("N");
        var stream = $"EvalRun-{evalId}";
        var now    = DateTimeOffset.UtcNow;

        // Write EvalRunStarted
        await AppendAsync(stream, "EvalRunStarted", new EvalRunStarted(sessionId, scorerName, criteria, now), ct).ConfigureAwait(false);

        // Score each turn
        var scoredTurns = new List<ScoredTurn>();

        foreach (var turn in turns) {
            var scored = await scorer(turn, ct).ConfigureAwait(false);
            scoredTurns.Add(scored);

            await AppendAsync(
                stream,
                "TurnScored",
                new TurnScored(
                    SessionId: sessionId,
                    TurnIndex: turn.Index,
                    Input: turn.UserInput,
                    Output: turn.AssistantOutput,
                    Score: scored.Score,
                    ScoreLabel: scored.Label,
                    Reason: scored.Reason,
                    Timestamp: DateTimeOffset.UtcNow
                ),
                ct
            ).ConfigureAwait(false);
        }

        var avgScore = scoredTurns.Count > 0 ? scoredTurns.Average(s => s.Score) : 0;

        // Write EvalRunCompleted
        await AppendAsync(
            stream,
            "EvalRunCompleted",
            new EvalRunCompleted(
                SessionId: sessionId,
                TurnsScored: scoredTurns.Count,
                AverageScore: avgScore,
                TotalCost: null,
                Timestamp: DateTimeOffset.UtcNow
            ),
            ct
        ).ConfigureAwait(false);

        return new(
            sessionId,
            scoredTurns,
            avgScore,
            turns.Where(t => t.InputTokens.HasValue).Sum(t => t.InputTokens!.Value),
            turns.Where(t => t.OutputTokens.HasValue).Sum(t => t.OutputTokens!.Value)
        );
    }

    /// <summary>
    /// Create an LLM-as-judge scorer that uses an IChatClient to evaluate each turn.
    /// </summary>
    public static Func<Turn, CancellationToken, Task<ScoredTurn>> LlmJudge(
            IChatClient chatClient,
            string      criteria,
            string?     model = null
        ) =>
        async (turn, ct) => {
            var toolContext = turn.ToolCalls.Count > 0
                ? $"\nTool calls made:\n{string.Join("\n", turn.ToolCalls.Select(tc => $"  - {tc.Name}({tc.Arguments}) → {tc.Result}"))}"
                : "";

            var prompt = $$"""
                           You are an AI evaluator. Score the following agent response on a scale of 0.0 to 1.0.

                           Criteria: {{criteria}}

                           User input: {{turn.UserInput}}
                           {{toolContext}}
                           Agent output: {{turn.AssistantOutput}}

                           Respond with ONLY a JSON object:
                           {"score": <0.0-1.0>, "label": "<good|acceptable|poor>", "reason": "<brief explanation>"}
                           """;

            var response = await chatClient.GetResponseAsync(prompt, new() { ModelId = model }, ct).ConfigureAwait(false);
            var text     = response.Text.Trim();

            try {
                var result = JsonSerializer.Deserialize<JsonElement>(text);
                var score  = result.GetProperty("score").GetDouble();
                var label  = result.TryGetProperty("label", out var l) ? l.GetString() : null;
                var reason = result.TryGetProperty("reason", out var r) ? r.GetString() : null;

                return new(turn, score, label, reason);
            } catch {
                // Fallback if LLM doesn't return valid JSON
                return new(turn, 0.5, "parse_error", $"Could not parse judge response: {text}");
            }
        };

    async Task AppendAsync<T>(string stream, string eventType, T payload, CancellationToken ct) {
        var data = JsonSerializer.SerializeToUtf8Bytes(payload, JsonOptions);

        await client.AppendToStreamAsync(
                stream,
                StreamState.Any,
                [new(Uuid.NewUuid(), eventType, data)],
                cancellationToken: ct
            )
            .ConfigureAwait(false);
    }
}
