// Multi-agent coordination via KurrentDB streams.
// Demo 1: StreamCoordinator — cross-process task distribution and result collection.
// Demo 2: KurrentDBGroupChatManager — durable group chat with auditable turn history.
// No LLM required — uses deterministic executors.

using System.Reflection;
using System.Text;
using System.Text.Json;
using Kurrent.AgentFramework;
using Kurrent.AgentFramework.Workflows;
using KurrentDB.Client;
using Microsoft.Agents.AI;
using Microsoft.Agents.AI.Workflows;
using Microsoft.Extensions.AI;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;

// --- Build host ---
var builder = Host.CreateApplicationBuilder(args);
builder.Services.AddKurrentAgentFramework(builder.Configuration);
var host = builder.Build();

var kurrentDb = host.Services.GetRequiredService<KurrentDBClient>();

// ============================================================
// DEMO 1: StreamCoordinator — Task distribution via streams
// ============================================================
Console.WriteLine("========================================");
Console.WriteLine("DEMO 1: Task Distribution via Streams");
Console.WriteLine("========================================\n");

var coordinator  = new StreamCoordinator(kurrentDb);
var taskStream   = $"AgentTasks-{Guid.NewGuid():N}";
var resultStream = $"AgentResults-{Guid.NewGuid():N}";

// Publish work items
var tasks = new[] {
    new TaskItem("task-1", "Summarize the Q4 earnings report"),
    new TaskItem("task-2", "Draft a response to the customer complaint"),
    new TaskItem("task-3", "Review the pull request for security issues"),
};

foreach (var task in tasks) {
    await coordinator.PublishAsync(taskStream, "TaskAssigned", task);
    Console.WriteLine($"  Published: {task.TaskId} — {task.Description}");
}

// Simulate an agent consuming tasks and producing results
Console.WriteLine("\n  Agent processing tasks...\n");

var subscription = kurrentDb.SubscribeToStream(taskStream, FromStream.Start);

var processed = 0;

await foreach (var message in subscription.Messages) {
    if (message is not StreamMessage.Event(var resolvedEvent)) continue;

    var task = JsonSerializer.Deserialize<TaskItem>(
        resolvedEvent.Event.Data.Span,
        new JsonSerializerOptions { PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower }
    );

    if (task is null) continue;

    // Simulate agent work
    var result = new TaskResult(task.TaskId, $"Completed: {task.Description}", "success");

    await coordinator.PublishResultAsync(resultStream, task.TaskId, result);
    Console.WriteLine($"  Processed: {task.TaskId} → result published to {resultStream}");

    if (++processed >= tasks.Length) break;
}

// Read results
Console.WriteLine($"\n  Results in {resultStream}:\n");

await DumpStream(resultStream);

// ============================================================
// DEMO 2: Durable Group Chat via KurrentDB
// ============================================================
Console.WriteLine("========================================");
Console.WriteLine("DEMO 2: Durable Group Chat");
Console.WriteLine("========================================\n");

var chatId = Guid.NewGuid().ToString("N");

// Create simple executor-based "agents" that simulate different roles
var analyst  = new SimulatedAgent("Analyst", "Based on Q4 data: revenue up 15%, costs down 3%. Recommend: proceed with expansion.");
var reviewer = new SimulatedAgent("Reviewer", "Review complete. Financials look solid. One concern: expansion timing given market volatility.");
var manager  = new SimulatedAgent("Manager", "Decision: approve expansion with 6-month staged rollout to mitigate risk.");

var agents = new List<SimulatedAgent> { analyst, reviewer, manager };

// Round-robin selection
var groupChatManager = new KurrentDBGroupChatManager(
    client: kurrentDb,
    chatId: chatId,
    agents: agents,
    selectNext: (history,      agentList, iteration) => agentList[iteration % agentList.Count],
    shouldTerminate: (history, iteration) => iteration >= 3,
    maxIterations: 3
);

// Simulate the group chat manually (since we're not using real LLM agents)
Console.WriteLine("  Simulating group chat...\n");

var chatHistory = new List<ChatMessage> {
    new(ChatRole.User, "Analyze Q4 results and decide on expansion."),
};

for (var i = 0; i < 3; i++) {
    var selected = await CallSelectNext(groupChatManager, chatHistory);
    var response = ((SimulatedAgent)selected).Response;

    Console.WriteLine($"  [{selected.Name}]: {response}");

    chatHistory.Add(new(ChatRole.Assistant, response) { AuthorName = selected.Name });
}

// Check termination
var terminated = await CallShouldTerminate(groupChatManager, chatHistory);
Console.WriteLine($"\n  Chat terminated: {terminated}");

// Read the durable chat history from KurrentDB
Console.WriteLine($"\n  Group chat events in KurrentDB (GroupChat-{chatId}):\n");

await DumpStream($"GroupChat-{chatId}");

return;

// --- Helpers ---

async Task DumpStream(string streamName) {
    try {
        var events   = kurrentDb.ReadStreamAsync(Direction.Forwards, streamName, StreamPosition.Start);
        var position = 0;

        await foreach (var resolvedEvent in events) {
            var data                    = Encoding.UTF8.GetString(resolvedEvent.Event.Data.Span);
            if (data.Length > 120) data = data[..120] + "...";
            Console.WriteLine($"    [{position}] {resolvedEvent.Event.EventType}");
            Console.WriteLine($"         {data}");

            if (resolvedEvent.Event.Metadata.Length > 0)
                Console.WriteLine($"         meta: {Encoding.UTF8.GetString(resolvedEvent.Event.Metadata.Span)}");

            position++;
        }
    } catch (StreamNotFoundException) {
        Console.WriteLine("    (stream not found)");
    }

    Console.WriteLine();
}

// Reflection helpers to call protected internal methods for the demo
async Task<AIAgent> CallSelectNext(KurrentDBGroupChatManager mgr, IReadOnlyList<ChatMessage> history) {
    var method = typeof(GroupChatManager).GetMethod("SelectNextAgentAsync", BindingFlags.Instance | BindingFlags.NonPublic)!;
    var task   = (ValueTask<AIAgent>)method.Invoke(mgr, [history, CancellationToken.None])!;
    var result = await task;
    // Increment iteration manually since we're outside the workflow engine
    var iterProp = typeof(GroupChatManager).GetProperty("IterationCount")!;
    iterProp.SetValue(mgr, (int)iterProp.GetValue(mgr)! + 1);

    return result;
}

async Task<bool> CallShouldTerminate(KurrentDBGroupChatManager mgr, IReadOnlyList<ChatMessage> history) {
    var method = typeof(GroupChatManager).GetMethod("ShouldTerminateAsync", BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic)!;
    var task   = (ValueTask<bool>)method.Invoke(mgr, [history, CancellationToken.None])!;

    return await task;
}

// --- Types ---

sealed record TaskItem(string TaskId, string Description);

sealed record TaskResult(string TaskId, string Result, string Status);

/// <summary>
/// Simple simulated agent with a fixed response, for demo without LLM.
/// </summary>
sealed class SimulatedAgent(string name, string response) : AIAgent {
    public string Response => response;

    public override string? Name => name;

    protected override ValueTask<AgentSession> CreateSessionCoreAsync(CancellationToken ct) =>
        new(new SimpleSession());

    protected override ValueTask<JsonElement> SerializeSessionCoreAsync(AgentSession session, JsonSerializerOptions? opts, CancellationToken ct) =>
        new(JsonSerializer.SerializeToElement(new { }));

    protected override ValueTask<AgentSession> DeserializeSessionCoreAsync(JsonElement state, JsonSerializerOptions? opts, CancellationToken ct) =>
        new(new SimpleSession());

    protected override Task<AgentResponse> RunCoreAsync(IEnumerable<ChatMessage> messages, AgentSession? session, AgentRunOptions? options, CancellationToken ct) =>
        Task.FromResult(new AgentResponse(new ChatMessage(ChatRole.Assistant, response)));

    protected override IAsyncEnumerable<AgentResponseUpdate> RunCoreStreamingAsync(IEnumerable<ChatMessage> messages, AgentSession? session, AgentRunOptions? options, CancellationToken ct) =>
        throw new NotImplementedException();

    sealed class SimpleSession : AgentSession;
}
