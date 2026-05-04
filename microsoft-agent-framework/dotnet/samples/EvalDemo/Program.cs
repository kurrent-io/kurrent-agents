// Lightweight eval tool demo.
// First: creates a synthetic agent session with known turns in KurrentDB.
// Then: runs a heuristic IEvaluator against it, scoring each turn.
// Per-metric scores are written back as TurnScored events in an EvalRun-{id} stream.
// No LLM required — see HybridEvalDemo for an LLM-judge example.

using System.Text;
using EvalDemo;
using Google.Protobuf.WellKnownTypes;
using Kurrent.Agent.Schema;
using Kurrent.Agent.Schema.Events;
using Kurrent.AgentFramework;
using Kurrent.AgentFramework.Eval;
using Kurrent.AgentFramework.Serialization;
using KurrentDB.Client;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;

// --- Build host ---
var builder = Host.CreateApplicationBuilder(args);
builder.Services.AddKurrentAgentFramework(builder.Configuration);
var host = builder.Build();

var kurrentDb = host.Services.GetRequiredService<KurrentDBClient>();

// ============================================================
// Step 1: Create a synthetic session with known turns
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
    EventSerializer.Serialize(new SessionStarted { AgentName = "EvalTestAgent", Model = "test-model", Timestamp = pNow }),

    // Turn 1: good response
    EventSerializer.Serialize(UserMsg("What's the weather in London?", 0)),
    EventSerializer.Serialize(toolCalls),
    EventSerializer.Serialize(
        new ToolResultReceived {
            CallId       = "call-1",
            ToolName     = "GetWeather",
            Result       = "Sunny, 22°C",
            MessageIndex = 2,
            Timestamp    = pNow,
        }
    ),
    EventSerializer.Serialize(AsstText("The weather in London is sunny at 22°C.", 3)),

    // Turn 2: poor response (empty)
    EventSerializer.Serialize(UserMsg("Tell me a joke", 4)),
    EventSerializer.Serialize(AsstText("", 5)),

    // Turn 3: acceptable but missed tool usage
    EventSerializer.Serialize(UserMsg("What time is it in Tokyo?", 6)),
    EventSerializer.Serialize(AsstText("I'm not sure of the exact time right now.", 7)),

    // Turn 4: good response with facts
    EventSerializer.Serialize(UserMsg("What is my name?", 8)),
    EventSerializer.Serialize(AsstText("Your name is Alexey and you work at Kurrent.", 9)),

    EventSerializer.Serialize(new SessionEnded { Reason = "completed", Timestamp = pNow }),
};

await kurrentDb.AppendToStreamAsync(streamName, StreamState.Any, events);
Console.WriteLine($"  Written {events.Count} events\n");

// ============================================================
// Step 2: Read turns from the session
// ============================================================
Console.WriteLine("========================================");
Console.WriteLine("Extracted turns");
Console.WriteLine("========================================\n");

var turns = await SessionTurnReader.ReadTurnsAsync(kurrentDb, sessionId);

foreach (var turn in turns) {
    Console.WriteLine($"  Turn {turn.Index}:");
    Console.WriteLine($"    Input:  {turn.UserInput}");
    Console.WriteLine($"    Output: {turn.AssistantOutput ?? "(empty)"}");
    Console.WriteLine($"    Tools:  {(turn.ToolCalls.Count > 0 ? string.Join(", ", turn.ToolCalls.Select(tc => tc.Name)) : "none")}");
    Console.WriteLine();
}

// ============================================================
// Step 3: Run the heuristic evaluator
// ============================================================
Console.WriteLine("========================================");
Console.WriteLine("Running heuristic eval");
Console.WriteLine("========================================\n");

var evalRunner = new EvalRunner(kurrentDb);

var result = await evalRunner.RunAsync(
    sessionId,
    scorerName: "heuristic-v1",
    criteria: "Response quality: completeness, tool usage, helpfulness",
    evaluator: new DemoHeuristicEvaluator()
);

foreach (var scored in result.ScoredMetrics) {
    var turn   = scored.Turn!; // RunAsync always populates Turn for per-turn results
    var rating = scored.InterpretationRating is null ? "" : $" [{scored.InterpretationRating}]";
    Console.WriteLine($"  Turn {turn.Index} · {scored.MetricName}: {scored.Score:F2}{rating}");
    Console.WriteLine($"    Input:  {turn.UserInput}");
    Console.WriteLine($"    Output: {turn.AssistantOutput ?? "(empty)"}");

    if (!string.IsNullOrEmpty(scored.Reason))
        Console.WriteLine($"    Reason: {scored.Reason}");
    Console.WriteLine();
}

Console.WriteLine("  Per-metric averages:");

foreach (var (name, value) in result.PerMetricAverage)
    Console.WriteLine($"    {name}: {value:F2}");
Console.WriteLine($"  Total tokens:  {result.TotalInputTokens ?? 0} in / {result.TotalOutputTokens ?? 0} out");

// ============================================================
// Step 4: Show eval events in KurrentDB
// ============================================================
Console.WriteLine("\n========================================");
Console.WriteLine("Eval events in KurrentDB");
Console.WriteLine("========================================\n");

var allStreams = kurrentDb.ReadAllAsync(Direction.Backwards, Position.End, maxCount: 100);

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

    break; // just show the first eval run
}
