// Session-level eval demo.
// Seeds a synthetic agent session with known turns + tool calls, then evaluates
// the WHOLE session as a unit using a heuristic IEvaluator.
// One SessionScored event per metric is written to the EvalRun-{id} stream.

using System.Text;
using Google.Protobuf.WellKnownTypes;
using Kurrent.Agent.Schema;
using Kurrent.Agent.Schema.Events;
using Kurrent.AgentFramework;
using Kurrent.AgentFramework.Eval;
using Kurrent.AgentFramework.Serialization;
using KurrentDB.Client;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using SessionEvalDemo;

var builder = Host.CreateApplicationBuilder(args);
builder.Services.AddKurrentAgentFramework(builder.Configuration);
var host = builder.Build();

var kurrentDb = host.Services.GetRequiredService<KurrentDBClient>();

var sessionId  = Guid.NewGuid().ToString();
var streamName = StreamNames.AgentSession(sessionId);
var pNow       = Timestamp.FromDateTimeOffset(DateTimeOffset.UtcNow);

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
    EventSerializer.Serialize(new SessionStarted { AgentName = "SessionEvalAgent", Model = "test-model", Timestamp = pNow }),
    EventSerializer.Serialize(UserMsg("What's the weather in London?", 0)),
    EventSerializer.Serialize(toolCalls),
    EventSerializer.Serialize(new ToolResultReceived {
        CallId = "call-1", ToolName = "GetWeather", Result = "Sunny, 22°C",
        MessageIndex = 2, Timestamp = pNow,
    }),
    EventSerializer.Serialize(AsstText("The weather in London is sunny at 22°C.", 3)),
    EventSerializer.Serialize(UserMsg("Tell me more about UK weather patterns.", 4)),
    EventSerializer.Serialize(AsstText("UK weather is shaped by Atlantic systems and the Gulf Stream...", 5)),
    EventSerializer.Serialize(new SessionEnded { Reason = "completed", Timestamp = pNow }),
};

await kurrentDb.AppendToStreamAsync(streamName, StreamState.Any, events);
Console.WriteLine($"  Written {events.Count} events\n");

Console.WriteLine("========================================");
Console.WriteLine("Running session-level eval");
Console.WriteLine("========================================\n");

var evalRunner = new EvalRunner(kurrentDb);

var result = await evalRunner.RunSessionAsync(
    sessionId,
    scorerName: "session-heuristic-v1",
    criteria: "Whole-session quality (tool error rate, productivity)",
    evaluator: new DemoSessionEvaluator()
);

foreach (var scored in result.ScoredMetrics) {
    var rating = scored.InterpretationRating is null ? "" : $" [{scored.InterpretationRating}]";
    var value  = scored.MetricKind == "string" ? scored.StringValue : scored.Score.ToString("F2");
    Console.WriteLine($"  {scored.MetricName} = {value}{rating}");
    if (!string.IsNullOrEmpty(scored.Reason))
        Console.WriteLine($"    Reason: {scored.Reason}");
}

Console.WriteLine();
Console.WriteLine("  Per-metric averages (aggregable only):");
foreach (var (name, value) in result.PerMetricAverage)
    Console.WriteLine($"    {name}: {value:F2}");
Console.WriteLine($"  Total tokens: {result.TotalInputTokens ?? 0} in / {result.TotalOutputTokens ?? 0} out");

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
    break;
}
