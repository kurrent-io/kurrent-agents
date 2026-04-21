using System.Text.Json;

namespace Kurrent.Agent.Schema.Events;

/// <summary>Eval run start. Written to <c>EvalRun-{run_id}</c>.</summary>
public sealed record EvalRunStarted(
    string                                     SessionId,
    string                                     Scorer,
    string                                     Criteria,
    DateTimeOffset                             Timestamp,
    IReadOnlyDictionary<string, JsonElement>?  Extensions = null
);

/// <summary>Per-turn score within an eval run.</summary>
public sealed record TurnScored(
    string                                     SessionId,
    int                                        TurnIndex,
    string?                                    Input,
    string?                                    Output,
    double                                     Score,
    string?                                    ScoreLabel,
    string?                                    Reason,
    DateTimeOffset                             Timestamp,
    IReadOnlyDictionary<string, JsonElement>?  Extensions = null
);

/// <summary>Eval run completion summary.</summary>
public sealed record EvalRunCompleted(
    string                                     SessionId,
    int                                        TurnsScored,
    double                                     AverageScore,
    double?                                    TotalCost,
    DateTimeOffset                             Timestamp,
    IReadOnlyDictionary<string, JsonElement>?  Extensions = null
);
