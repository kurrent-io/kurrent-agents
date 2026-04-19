namespace Kurrent.AgentFramework.Memory;

/// <summary>
/// Storage contract for agent memory: recall facts relevant to a query,
/// and retain new facts.
/// </summary>
public interface IAgentMemory {
    /// <summary>
    /// Recall facts relevant to the given query. Implementations may perform
    /// full-text, vector, hybrid, or no search — or simply return everything.
    /// </summary>
    IAsyncEnumerable<string> RecallAsync(string query, CancellationToken ct = default);

    /// <summary>
    /// Retain a new fact so it can be recalled later.
    /// </summary>
    Task RetainAsync(string fact, CancellationToken ct = default);
}
