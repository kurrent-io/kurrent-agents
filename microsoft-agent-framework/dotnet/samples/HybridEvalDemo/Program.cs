// Hybrid eval demo: heuristic first, LLM judge only on ambiguity.
// Cheap signals (empty, clearly correct, tool errors) are decided by heuristics.
// Borderline cases (short answers, uncertain phrasing) are escalated to an LLM judge.
// The runner just sees one Func<Turn, ...> — composition lives in the scorer.

using System.Text;
using Anthropic;
using Kurrent.AgentFramework;
using Kurrent.AgentFramework.Eval;
using Kurrent.AgentFramework.Events;
using KurrentDB.Client;
using Microsoft.Extensions.AI;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using static Kurrent.AgentFramework.Serialization.EventSerializer;

// --- Build host ---
var builder = Host.CreateApplicationBuilder(args);
builder.Services.AddKurrentAgentFramework(builder.Configuration);
var host = builder.Build();

var kurrentDb = host.Services.GetRequiredService<KurrentDBClient>();
var config    = builder.Configuration;

var anthropicKey = config["Anthropic:ApiKey"]
 ?? throw new InvalidOperationException("Anthropic API key not found in configuration.");

var model = config["Anthropic:Model"] ?? "claude-sonnet-4-20250514";

IAnthropicClient anthropic   = new AnthropicClient(new() { ApiKey = anthropicKey });
var              judgeClient = anthropic.AsIChatClient(model, 1024);

// ============================================================
// Step 1: Synthetic session covering confident + ambiguous turns
// ============================================================
var sessionId  = Guid.NewGuid().ToString();
var streamName = StreamName.ForSession(sessionId);
var now        = DateTimeOffset.UtcNow;

Console.WriteLine("========================================");
Console.WriteLine("Creating synthetic agent session");
Console.WriteLine($"Stream: {streamName}");
Console.WriteLine("========================================\n");

var events = new List<EventData> {
    Serialize(new SessionStarted("HybridEvalAgent", "test-model", null, null, now)),

    // Turn 0 — confident pass: long answer + correct tool call
    Serialize(new UserMessageReceived("What's the weather in London?", null, null, null, 0, now)),
    Serialize(new AssistantToolCallsGenerated([new("call-1", "GetWeather", null)], null, null, null, null, 1, now)),
    Serialize(new ToolResultReceived("call-1", "GetWeather", "Sunny, 22°C", null, null, null, 2, now)),
    Serialize(new AssistantTextGenerated("The weather in London is sunny at around 22°C right now.", null, null, null, 3, now)),

    // Turn 1 — confident fail: empty response
    Serialize(new UserMessageReceived("Tell me a joke", null, null, null, 4, now)),
    Serialize(new AssistantTextGenerated("", null, null, null, 5, now)),

    // Turn 2 — ambiguous: short answer that may or may not be acceptable
    Serialize(new UserMessageReceived("Is Paris the capital of France?", null, null, null, 6, now)),
    Serialize(new AssistantTextGenerated("Yes.", null, null, null, 7, now)),

    // Turn 3 — ambiguous: medium-length answer that hedges instead of using a tool
    Serialize(new UserMessageReceived("What time is it in Tokyo?", null, null, null, 8, now)),
    Serialize(new AssistantTextGenerated("Tokyo is in JST, which is UTC+9, so you can work it out from your local time.", null, null, null, 9, now)),

    // Turn 4 — ambiguous: plausible-sounding but factually wrong
    Serialize(new UserMessageReceived("Who wrote Hamlet?", null, null, null, 10, now)),
    Serialize(new AssistantTextGenerated("Hamlet was written by Christopher Marlowe in the late 1500s.", null, null, null, 11, now)),

    Serialize(new SessionEnded("completed", now)),
};

await kurrentDb.AppendToStreamAsync(streamName, StreamState.Any, events);
Console.WriteLine($"  Written {events.Count} events\n");

// ============================================================
// Step 2: Build the hybrid scorer
// ============================================================
const string criteria = "Response is helpful, factually correct, and uses tools when appropriate.";

var llmJudge = EvalRunner.LlmJudge(judgeClient, criteria, model);
var stats    = new ScorerStats();

// ============================================================
// Step 3: Run the eval
// ============================================================
Console.WriteLine("========================================");
Console.WriteLine("Running hybrid eval (heuristic + LLM judge)");
Console.WriteLine("========================================\n");

var evalRunner = new EvalRunner(kurrentDb);

var result = await evalRunner.RunAsync(
    sessionId,
    scorerName: "hybrid-v1",
    criteria: criteria,
    scorer: Hybrid
);

foreach (var scored in result.ScoredTurns) {
    Console.WriteLine($"  Turn {scored.Turn.Index}: {scored.Score:F2} [{scored.Label}]");
    Console.WriteLine($"    Input:  {scored.Turn.UserInput}");
    Console.WriteLine($"    Output: {scored.Turn.AssistantOutput ?? "(empty)"}");

    if (!string.IsNullOrEmpty(scored.Reason))
        Console.WriteLine($"    Reason: {scored.Reason}");
    Console.WriteLine();
}

Console.WriteLine($"  Average score:    {result.AverageScore:F2}");
Console.WriteLine($"  Heuristic-only:   {stats.HeuristicOnly} turn(s)");
Console.WriteLine($"  Escalated to LLM: {stats.Escalated} turn(s)");

// ============================================================
// Step 4: Show eval events in KurrentDB
// ============================================================
Console.WriteLine("\n========================================");
Console.WriteLine("Eval events in KurrentDB");
Console.WriteLine("========================================\n");

var allStreams = kurrentDb.ReadAllAsync(Direction.Backwards, Position.End, maxCount: 200);

await foreach (var resolved in allStreams) {
    if (!resolved.Event.EventStreamId.StartsWith("EvalRun-")) continue;

    var evalStream = resolved.Event.EventStreamId;
    Console.WriteLine($"--- {evalStream} ---\n");

    var evalEvents = kurrentDb.ReadStreamAsync(Direction.Forwards, evalStream, StreamPosition.Start);
    var pos        = 0;

    await foreach (var evt in evalEvents) {
        var data                    = Encoding.UTF8.GetString(evt.Event.Data.Span);
        if (data.Length > 200) data = data[..200] + "...";
        Console.WriteLine($"  [{pos}] {evt.Event.EventType}");
        Console.WriteLine($"       {data}");
        pos++;
    }

    break;
}

return;

async Task<ScoredTurn> Hybrid(Turn turn, CancellationToken ct) {
    var heuristic = await DemoHeuristicScorer(turn, ct);

    // Confident bands → trust the heuristic, no LLM call.
    if (heuristic.Score is >= 0.85 or <= 0.15) {
        stats.HeuristicOnly++;

        return heuristic with { Reason = $"[heuristic] {heuristic.Reason}" };
    }

    // Ambiguous → escalate.
    stats.Escalated++;
    var llm = await llmJudge(turn, ct);

    return llm with {
        Reason = $"[llm | heuristic={heuristic.Score:F2}] {llm.Reason}"
    };
}

// --- Heuristic scorer (deterministic, no LLM) ---
// Returns extreme scores when confident, mid-band scores when uncertain —
// the hybrid wrapper uses the score band to decide whether to escalate.
static Task<ScoredTurn> DemoHeuristicScorer(Turn turn, CancellationToken ct) {
    var score   = 1.0;
    var reasons = new List<string>();

    if (string.IsNullOrWhiteSpace(turn.AssistantOutput)) {
        return Task.FromResult(new ScoredTurn(turn, 0.0, "poor", "empty response"));
    }

    var len = turn.AssistantOutput!.Length;

    switch (len) {
        case < 10:
            score = 0.5;
            reasons.Add("very short response");

            break;
        case < 40:
            score = 0.6;
            reasons.Add("short response");

            break;
    }

    var errorTools = turn.ToolCalls.Count(tc => tc.IsError);

    if (errorTools > 0) {
        score -= 0.2 * errorTools;
        reasons.Add($"{errorTools} tool error(s)");
    }

    var needsTool = turn.UserInput?.Contains("weather", StringComparison.OrdinalIgnoreCase) == true
     || turn.UserInput?.Contains("time", StringComparison.OrdinalIgnoreCase)                == true;

    if (needsTool && turn.ToolCalls.Count == 0) {
        score = Math.Min(score, 0.55);
        reasons.Add("expected tool call but none made");
    }

    score = Math.Clamp(score, 0.0, 1.0);

    var label = score >= 0.8
        ? "good"
        : score >= 0.5
            ? "acceptable"
            : "poor";

    return Task.FromResult(new ScoredTurn(turn, score, label, string.Join("; ", reasons)));
}

internal sealed class ScorerStats {
    public int HeuristicOnly;
    public int Escalated;
}
