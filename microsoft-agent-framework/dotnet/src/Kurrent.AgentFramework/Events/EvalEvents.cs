using System.Text.Json.Serialization;

namespace Kurrent.AgentFramework.Events;

/// <summary>
/// Emitted when an eval run starts against a session.
/// </summary>
public sealed record EvalRunStarted(
        [property: JsonPropertyName("session_id")] string         SessionId,
        [property: JsonPropertyName("scorer")]     string         Scorer,
        [property: JsonPropertyName("criteria")]   string         Criteria,
        [property: JsonPropertyName("timestamp")]  DateTimeOffset Timestamp
    );

/// <summary>
/// Emitted for each scored turn within an eval run.
/// </summary>
public sealed record TurnScored(
        [property: JsonPropertyName("session_id")]  string         SessionId,
        [property: JsonPropertyName("turn_index")]  int            TurnIndex,
        [property: JsonPropertyName("input")]       string?        Input,
        [property: JsonPropertyName("output")]      string?        Output,
        [property: JsonPropertyName("score")]       double         Score,
        [property: JsonPropertyName("score_label")] string?        ScoreLabel,
        [property: JsonPropertyName("reason")]      string?        Reason,
        [property: JsonPropertyName("timestamp")]   DateTimeOffset Timestamp
    );

/// <summary>
/// Emitted when an eval run completes.
/// </summary>
public sealed record EvalRunCompleted(
        [property: JsonPropertyName("session_id")]      string         SessionId,
        [property: JsonPropertyName("turns_scored")]    int            TurnsScored,
        [property: JsonPropertyName("average_score")]   double         AverageScore,
        [property: JsonPropertyName("total_cost")]      double?        TotalCost,
        [property: JsonPropertyName("timestamp")]       DateTimeOffset Timestamp
    );
