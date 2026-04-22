// KurrentDB + Microsoft Agent Framework integration demo.
// Shows: multi-turn conversation with tools, usage capture, and cross-session memory.
// Uses the default KurrentDB-backed agent memory — no external search dependencies.

using System.ComponentModel;
using System.Text;
using System.Text.RegularExpressions;
using Anthropic;
using Kurrent.Agent.Schema;
using Kurrent.AgentFramework;
using Kurrent.AgentFramework.Capture;
using Kurrent.AgentFramework.ChatHistory;
using Kurrent.AgentFramework.Memory;
using KurrentDB.Client;
using Microsoft.Agents.AI;
using Microsoft.Extensions.AI;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;

// --- Tools ---
[Description("Get the current weather for a location.")]
static string GetWeather([Description("City name")] string location) =>
    $"The weather in {location} is sunny, 22°C.";

[Description("Get the current time in a timezone.")]
static string GetTime([Description("IANA timezone, e.g. Europe/London")] string timezone) =>
    $"The current time in {timezone} is {DateTimeOffset.UtcNow:HH:mm} UTC.";

// RetainFact tool is created as a closure below to capture DI services

// --- Build host ---
var builder = Host.CreateApplicationBuilder(args);
builder.Services.AddKurrentAgentFramework(builder.Configuration);
builder.Services.AddKurrentAgentMemory(PersonalFactExtractor);
var host = builder.Build();

await host.StartAsync();

// --- Resolve services ---
var kurrentDb      = host.Services.GetRequiredService<KurrentDBClient>();
var memory         = host.Services.GetRequiredService<IAgentMemory>();
var memoryProvider = host.Services.GetRequiredService<AgentMemoryContextProvider>();
var config         = builder.Configuration;

var anthropicKey = config["Anthropic:ApiKey"]
 ?? throw new InvalidOperationException("Anthropic API key not found in configuration.");

var model = config["Anthropic:Model"] ?? "claude-sonnet-4-20250514";

IAnthropicClient anthropic = new AnthropicClient(new() { ApiKey = anthropicKey });

// ============================================================
// SESSION 1: Agent learns about the user, retains facts
// ============================================================
Console.WriteLine("========================================");
Console.WriteLine("SESSION 1: Learning about the user");
Console.WriteLine("========================================\n");

var session1Id = Guid.NewGuid().ToString();
var (agent1, history1) = CreateAgent(session1Id);
var session1 = await agent1.CreateSessionAsync();

var r1 = await agent1.RunAsync("My name is Alexey, I work at Kurrent, and I prefer dark mode in all my apps.", session1);
Console.WriteLine($"Agent: {r1}\n");

var r2 = await agent1.RunAsync("What's the weather in London?", session1);
Console.WriteLine($"Agent: {r2}\n");

await history1.EndSessionAsync("completed");

// Allow the fact-extraction background subscription to catch up
await Task.Delay(1000);

// ============================================================
// SESSION 2: New session — agent recalls from memory
// ============================================================
Console.WriteLine("========================================");
Console.WriteLine("SESSION 2: New session with memory recall");
Console.WriteLine("========================================\n");

var session2Id = Guid.NewGuid().ToString();
var (agent2, history2) = CreateAgent(session2Id);
var session2 = await agent2.CreateSessionAsync();

var r3 = await agent2.RunAsync("What do you know about my preferences?", session2);
Console.WriteLine($"Agent: {r3}\n");

var r4 = await agent2.RunAsync("What is my name and where do I work?", session2);
Console.WriteLine($"Agent: {r4}\n");

await history2.EndSessionAsync("completed");

// --- Dump events from both sessions ---
Console.WriteLine("========================================");
Console.WriteLine("EVENTS IN KURRENTDB");
Console.WriteLine("========================================\n");

await DumpStream(StreamNames.AgentSession(session1Id));
await DumpStream(StreamNames.AgentSession(session2Id));

await host.StopAsync();

return;

// --- Helpers ---

(AIAgent Agent, KurrentDBChatHistoryProvider History) CreateAgent(string sid) {
    var usageCapture = new UsageCapture();

    var tools = new AITool[] {
        AIFunctionFactory.Create(GetWeather),
        AIFunctionFactory.Create(GetTime),
        AIFunctionFactory.Create(
            [Description("Retain a fact in agent memory for future recall.")] async ([Description("A self-contained fact to remember.")] string fact) => {
                await memory.RetainAsync(fact);
                return $"Retained: {fact}";
            },
            name: "RetainFact"
        ),
    };

    var historyProvider = new KurrentDBChatHistoryProvider(kurrentDb, sid, usageCapture, agentName: "DemoAgent", modelName: model);

    AIAgent agent = new ChatClientAgent(
        usageCapture.Wrap(anthropic.AsIChatClient(model, 4096)),
        new ChatClientAgentOptions {
            Name = "DemoAgent",
            ChatOptions = new() {
                Instructions = """
                               You are a helpful assistant. Be concise. Use tools when relevant.
                               When the user tells you personal information (name, preferences, workplace),
                               use the RetainFact tool to remember it for future sessions.
                               """,
                Tools = tools,
            },
            ChatHistoryProvider = historyProvider,
            AIContextProviders  = [memoryProvider],
        }
    );

    return (agent, historyProvider);
}

async Task DumpStream(string streamName) {
    Console.WriteLine($"--- {streamName} ---\n");

    try {
        var events   = kurrentDb.ReadStreamAsync(Direction.Forwards, streamName, StreamPosition.Start);
        var position = 0;

        await foreach (var resolvedEvent in events) {
            Console.WriteLine($"  [{position}] {resolvedEvent.Event.EventType}");
            Console.WriteLine($"       data: {Encoding.UTF8.GetString(resolvedEvent.Event.Data.Span)}");

            if (resolvedEvent.Event.Metadata.Length > 0)
                Console.WriteLine($"       meta: {Encoding.UTF8.GetString(resolvedEvent.Event.Metadata.Span)}");

            position++;
        }
    } catch (StreamNotFoundException) {
        Console.WriteLine("  (stream not found)");
    }

    Console.WriteLine();
}

// --- Demo-specific fact extractor ---
// Extracts personal info from user messages via regex.
// Real applications should provide their own FactExtractor tailored to the domain.
static IEnumerable<string> PersonalFactExtractor(string message) {
    (Regex pattern, string template)[] patterns = [
        (NameRegex(),     "User's name is {0}"),
        (WorkRegex(),     "User works at {0}"),
        (PreferRegex(),   "User prefers {0}"),
        (LocationRegex(), "User lives in {0}"),
        (EmailRegex(),    "User's email is {0}"),
        (TimezoneRegex(), "User's timezone is {0}"),
    ];

    foreach (var (pattern, template) in patterns) {
        var match = pattern.Match(message);
        if (!match.Success) continue;

        var extracted = match.Groups[1].Value.Trim();
        if (!string.IsNullOrEmpty(extracted))
            yield return string.Format(template, extracted);
    }
}

partial class Program {
    [GeneratedRegex(@"(?:my name is|i'm|i am)\s+(\w+)", RegexOptions.IgnoreCase)]
    private static partial Regex NameRegex();

    [GeneratedRegex(@"i (?:work|am working) (?:at|for)\s+(.+?)(?:\.|,|$)", RegexOptions.IgnoreCase)]
    private static partial Regex WorkRegex();

    [GeneratedRegex(@"i (?:prefer|like|love|use)\s+(.+?)(?:\.|,|$)", RegexOptions.IgnoreCase)]
    private static partial Regex PreferRegex();

    [GeneratedRegex(@"i (?:live|am based|am located) (?:in|at)\s+(.+?)(?:\.|,|$)", RegexOptions.IgnoreCase)]
    private static partial Regex LocationRegex();

    [GeneratedRegex(@"my (?:email|e-mail) (?:is|address is)\s+(\S+)", RegexOptions.IgnoreCase)]
    private static partial Regex EmailRegex();

    [GeneratedRegex(@"my (?:timezone|time zone|tz) is\s+(.+?)(?:\.|,|$)", RegexOptions.IgnoreCase)]
    private static partial Regex TimezoneRegex();
}
