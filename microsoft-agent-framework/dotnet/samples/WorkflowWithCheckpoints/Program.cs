// KurrentDB-backed workflow checkpointing demo.
// Shows: a multi-step workflow with durable checkpoints in KurrentDB,
// then resumes from a saved checkpoint (simulating process restart).

using System.Text;
using Kurrent.AgentFramework;
using Kurrent.AgentFramework.Workflows;
using KurrentDB.Client;
using Microsoft.Agents.AI.Workflows;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;

// --- Build host ---
var builder = Host.CreateApplicationBuilder(args);
builder.Services.AddKurrentAgentFramework(builder.Configuration);
var host = builder.Build();

var kurrentDb = host.Services.GetRequiredService<KurrentDBClient>();

// --- Create KurrentDB-backed checkpoint manager ---
var checkpointManager = KurrentDBCheckpointManagerFactory.Create(kurrentDb);

// --- Build a document processing pipeline ---
var classifier = new ClassifyExecutor();
var processor  = new ProcessExecutor();
var reviewer   = new ReviewExecutor();

var workflow = new WorkflowBuilder(classifier)
    .AddEdge(classifier, processor)
    .AddEdge(processor, reviewer)
    .WithOutputFrom(reviewer)
    .Build();

// === RUN 1: Execute workflow, collect checkpoints ===
Console.WriteLine("========================================");
Console.WriteLine("RUN 1: Execute workflow with checkpoints");
Console.WriteLine("========================================\n");

var checkpoints = new List<CheckpointInfo>();

await using (var run = await InProcessExecution.RunStreamingAsync(workflow, "Process this important document about Q4 earnings.", checkpointManager)) {
    await foreach (var evt in run.WatchStreamAsync()) {
        switch (evt) {
            case ExecutorInvokedEvent invoked:
                Console.WriteLine($"  > {invoked.ExecutorId} started");

                break;

            case ExecutorCompletedEvent completed:
                Console.WriteLine($"  < {completed.ExecutorId} completed: {completed.Data}");

                break;

            case SuperStepCompletedEvent superStep:
                if (superStep.CompletionInfo?.Checkpoint is { } cp) {
                    checkpoints.Add(cp);
                    Console.WriteLine($"  ** Checkpoint saved: {cp.CheckpointId[..8]}...");
                }

                break;

            case WorkflowOutputEvent output:
                Console.WriteLine($"\n  Result: {output.Data}");

                break;
        }
    }
}

Console.WriteLine($"\nTotal checkpoints: {checkpoints.Count}");

// --- Verify checkpoints are in KurrentDB ---
Console.WriteLine("\n========================================");
Console.WriteLine("CHECKPOINTS IN KURRENTDB");
Console.WriteLine("========================================\n");

var streamName = $"WorkflowCheckpoint-{checkpoints[0].SessionId}";

try {
    var events   = kurrentDb.ReadStreamAsync(Direction.Forwards, streamName, StreamPosition.Start);
    var position = 0;

    await foreach (var resolvedEvent in events) {
        var meta                                  = Encoding.UTF8.GetString(resolvedEvent.Event.Metadata.Span);
        var dataPreview                           = Encoding.UTF8.GetString(resolvedEvent.Event.Data.Span);
        if (dataPreview.Length > 100) dataPreview = dataPreview[..100] + "...";

        Console.WriteLine($"  [{position}] {resolvedEvent.Event.EventType}");
        Console.WriteLine($"       meta: {meta}");
        Console.WriteLine($"       data: {dataPreview}");
        position++;
    }
} catch (StreamNotFoundException) {
    Console.WriteLine("  (stream not found)");
}

// === RUN 2: Resume from the first checkpoint (simulating restart) ===
if (checkpoints.Count >= 1) {
    Console.WriteLine("\n========================================");
    Console.WriteLine("RUN 2: Resume from checkpoint (after classifier)");
    Console.WriteLine("========================================\n");

    // Build a fresh workflow (simulating a new process)
    var newClassifier = new ClassifyExecutor();
    var newProcessor  = new ProcessExecutor();
    var newReviewer   = new ReviewExecutor();

    var newWorkflow = new WorkflowBuilder(newClassifier)
        .AddEdge(newClassifier, newProcessor)
        .AddEdge(newProcessor, newReviewer)
        .WithOutputFrom(newReviewer)
        .Build();

    var resumeFrom = checkpoints[0]; // after classifier
    Console.WriteLine($"  Resuming from checkpoint: {resumeFrom.CheckpointId[..8]}...\n");

    await using var resumedRun = await InProcessExecution.ResumeStreamingAsync(newWorkflow, resumeFrom, checkpointManager);

    await foreach (var evt in resumedRun.WatchStreamAsync()) {
        switch (evt) {
            case ExecutorInvokedEvent invoked:
                Console.WriteLine($"  > {invoked.ExecutorId} started");

                break;

            case ExecutorCompletedEvent completed:
                Console.WriteLine($"  < {completed.ExecutorId} completed: {completed.Data}");

                break;

            case WorkflowOutputEvent output:
                Console.WriteLine($"\n  Result: {output.Data}");

                break;
        }
    }
}

// === Executors ===

[SendsMessage(typeof(string))]
sealed class ClassifyExecutor() : Executor<string>("Classifier") {
    public override async ValueTask HandleAsync(string message, IWorkflowContext context, CancellationToken ct = default) {
        // Simulate classification
        await Task.Delay(100, ct);
        var classified = $"[classified:financial] {message}";
        await context.SendMessageAsync(classified, cancellationToken: ct);
    }
}

[SendsMessage(typeof(string))]
sealed class ProcessExecutor() : Executor<string>("Processor") {
    public override async ValueTask HandleAsync(string message, IWorkflowContext context, CancellationToken ct = default) {
        // Simulate processing
        await Task.Delay(100, ct);
        var processed = $"[processed] {message} → extracted: revenue up 15%, costs down 3%";
        await context.SendMessageAsync(processed, cancellationToken: ct);
    }
}

[YieldsOutput(typeof(string))]
sealed class ReviewExecutor() : Executor<string>("Reviewer") {
    public override async ValueTask HandleAsync(string message, IWorkflowContext context, CancellationToken ct = default) {
        // Simulate review
        await Task.Delay(100, ct);
        var reviewed = $"[reviewed] {message} → APPROVED for distribution";
        await context.YieldOutputAsync(reviewed, cancellationToken: ct);
    }
}
