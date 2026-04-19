using System.Text;
using Microsoft.Agents.AI;
using Microsoft.Extensions.AI;

namespace Kurrent.AgentFramework.Memory;

/// <summary>
/// <see cref="AIContextProvider"/> that recalls facts from an <see cref="IAgentMemory"/>
/// before each agent run and injects them as system instructions.
/// </summary>
public sealed class AgentMemoryContextProvider(IAgentMemory memory) : AIContextProvider {
    protected override async ValueTask<AIContext> ProvideAIContextAsync(
            InvokingContext   context,
            CancellationToken cancellationToken = default
        ) {
        var userMessage = context.AIContext.Messages?
            .LastOrDefault(m => m.Role == ChatRole.User)?.Text;

        if (string.IsNullOrWhiteSpace(userMessage))
            return new();

        var sb          = new StringBuilder();
        var wroteHeader = false;

        await foreach (var fact in memory.RecallAsync(userMessage, cancellationToken).ConfigureAwait(false)) {
            if (!wroteHeader) {
                sb.AppendLine("Previously retained knowledge (may be outdated — verify if unsure):");
                wroteHeader = true;
            }

            sb.AppendLine($"- {fact}");
        }

        return wroteHeader ? new() { Instructions = sb.ToString() } : new();
    }

    /// <summary>
    /// Post-run retention is a no-op. Wire up fact extraction via
    /// <c>FactExtractionService</c> (event-driven) or a tool the agent can call.
    /// </summary>
    protected override ValueTask StoreAIContextAsync(
            InvokedContext    context,
            CancellationToken cancellationToken = default
        ) => default;
}
