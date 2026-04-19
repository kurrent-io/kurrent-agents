namespace Kurrent.AgentFramework;

/// <summary>
/// Conventions for KurrentDB stream names.
/// </summary>
public static class StreamName {
    /// <summary>
    /// Stream for a single agent session's events.
    /// Category prefix <c>AgentSession-</c> is used by <c>FactExtractionService</c>
    /// as a server-side stream-prefix filter on <c>$all</c>.
    /// </summary>
    public static string ForSession(string sessionId) => $"AgentSession-{sessionId}";
}
