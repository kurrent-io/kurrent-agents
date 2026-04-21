namespace Kurrent.Agent.Schema;

/// <summary>
/// Canonical stream-name builders. See <c>schema/SCHEMA_v2.md §2</c>.
/// </summary>
public static class StreamNames {
    public const string AgentSessionPrefix    = "AgentSession-";
    public const string AgentSubsessionPrefix = "AgentSubsession-";
    public const string AgentMemoryPrefix     = "AgentMemory-";
    public const string AgentArtifactPrefix   = "AgentArtifact-";
    public const string EvalRunPrefix         = "EvalRun-";

    public static string AgentSession(string sessionId) =>
        $"{AgentSessionPrefix}{sessionId}";

    public static string AgentSubsession(string parentSessionId, string agentId) =>
        $"{AgentSubsessionPrefix}{parentSessionId}-{agentId}";

    public static string AgentMemory(string appName, string userId) =>
        $"{AgentMemoryPrefix}{appName}-{userId}";

    public static string AgentArtifact(string scope, string filename) =>
        $"{AgentArtifactPrefix}{scope}-{filename}";

    public static string EvalRun(string runId) =>
        $"{EvalRunPrefix}{runId}";
}
