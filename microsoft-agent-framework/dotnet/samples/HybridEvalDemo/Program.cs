// Hybrid eval demo: heuristic first, LLM-based evaluators only on ambiguity.
// Cheap signals (empty, clearly correct, tool errors) come from a heuristic IEvaluator.
// Borderline cases escalate to RelevanceEvaluator + CoherenceEvaluator from
// Microsoft.Extensions.AI.Evaluation.Quality. The runner just sees one IEvaluator —
// composition lives in HybridEvaluator below.

using System.Text;
using Anthropic;
using Google.Protobuf.WellKnownTypes;
using HybridEvalDemo;
using Kurrent.Agent.Schema;
using Kurrent.Agent.Schema.Events;
using Kurrent.AgentFramework;
using Kurrent.AgentFramework.Eval;
using KurrentDB.Client;
using Microsoft.Extensions.AI;
using Microsoft.Extensions.AI.Evaluation;
using Microsoft.Extensions.AI.Evaluation.Quality;
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

IAnthropicClient anthropic         = new AnthropicClient(new() { ApiKey = anthropicKey });
var              judgeClient       = anthropic.AsIChatClient(model, 1024);
var              chatConfiguration = new ChatConfiguration(judgeClient);

// ============================================================
// Step 1: Synthetic session covering confident + ambiguous turns
// ============================================================
var sessionId  = Guid.NewGuid().ToString();
var streamName = StreamNames.AgentSession(sessionId);
var now        = DateTimeOffset.UtcNow;
var pNow       = Timestamp.FromDateTimeOffset(now);

UserMessageReceived UserMsg(string content, int idx) =>
    new() { Content = content, MessageIndex = idx, Timestamp = pNow };

AssistantTextGenerated AsstText(string content, int idx) =>
    new() { Content = content, MessageIndex = idx, Timestamp = pNow };

Console.WriteLine("========================================");
Console.WriteLine("Creating synthetic agent session");
Console.WriteLine($"Stream: {streamName}");
Console.WriteLine("========================================\n");

var toolCalls = new AssistantToolCallsGenerated { MessageIndex = 1, Timestamp = pNow };
toolCalls.ToolCalls.Add(new ToolCallInfo { CallId = "call-1", ToolName = "GetWeather" });

var events = new List<EventData> {
    Serialize(new SessionStarted { AgentName = "HybridEvalAgent", Model = "test-model", Timestamp = pNow }),

    // Turn 0 — confident pass: long answer + correct tool call
    Serialize(UserMsg("What's the weather in London?", 0)),
    Serialize(toolCalls),
    Serialize(new ToolResultReceived {
        CallId = "call-1", ToolName = "GetWeather", Result = "Sunny, 22°C",
        MessageIndex = 2, Timestamp = pNow,
    }),
    Serialize(AsstText("The weather in London is sunny at around 22°C right now.", 3)),

    // Turn 1 — confident fail: empty response
    Serialize(UserMsg("Tell me a joke", 4)),
    Serialize(AsstText("", 5)),

    // Turn 2 — ambiguous: short answer that may or may not be acceptable
    Serialize(UserMsg("Is Paris the capital of France?", 6)),
    Serialize(AsstText("Yes.", 7)),

    // Turn 3 — ambiguous: medium-length answer that hedges instead of using a tool
    Serialize(UserMsg("What time is it in Tokyo?", 8)),
    Serialize(AsstText("Tokyo is in JST, which is UTC+9, so you can work it out from your local time.", 9)),

    // Turn 4 — ambiguous: plausible-sounding but factually wrong
    Serialize(UserMsg("Who wrote Hamlet?", 10)),
    Serialize(AsstText("Hamlet was written by Christopher Marlowe in the late 1500s.", 11)),

    Serialize(new SessionEnded { Reason = "completed", Timestamp = pNow }),
};

await kurrentDb.AppendToStreamAsync(streamName, StreamState.Any, events);
Console.WriteLine($"  Written {events.Count} events\n");

// ============================================================
// Step 2: Build the hybrid evaluator
// ============================================================
const string criteria = "Response is helpful, factually correct, and uses tools when appropriate.";

var stats = new ScorerStats();
var hybrid = new HybridEvaluator(
    heuristic: new DemoHeuristicEvaluator(),
    onEscalation: () => stats.Escalated++,
    onConfident:  () => stats.HeuristicOnly++,
    new RelevanceEvaluator(),
    new CoherenceEvaluator()
);

// ============================================================
// Step 3: Run the eval
// ============================================================
Console.WriteLine("========================================");
Console.WriteLine("Running hybrid eval (heuristic + LLM judge on ambiguity)");
Console.WriteLine("========================================\n");

var result = await new EvalRunner(kurrentDb).RunAsync(
    sessionId,
    scorerName: "hybrid-v1",
    criteria: criteria,
    evaluator: hybrid,
    chatConfiguration: chatConfiguration
);

foreach (var grouped in result.ScoredMetrics.GroupBy(s => s.Turn.Index)) {
    var turn = grouped.First().Turn;
    Console.WriteLine($"  Turn {turn.Index}");
    Console.WriteLine($"    Input:  {turn.UserInput}");
    Console.WriteLine($"    Output: {turn.AssistantOutput ?? "(empty)"}");

    foreach (var scored in grouped) {
        var rating = scored.InterpretationRating is null ? "" : $" [{scored.InterpretationRating}]";
        var reason = string.IsNullOrEmpty(scored.Reason) ? "" : $" — {scored.Reason}";
        Console.WriteLine($"    {scored.MetricName}: {scored.Score:F2}{rating}{reason}");
    }
    Console.WriteLine();
}

Console.WriteLine("  Per-metric averages:");
foreach (var (name, value) in result.PerMetricAverage)
    Console.WriteLine($"    {name}: {value:F2}");
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

internal sealed class ScorerStats {
    public int HeuristicOnly;
    public int Escalated;
}
