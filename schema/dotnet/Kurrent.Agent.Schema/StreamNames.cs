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

    /// <summary>
    /// Normalises a stream-name id component per <c>SCHEMA_v2.md §2.4</c>: a GUID-shaped
    /// value is rewritten to the lowercase, dashless 32-char form (.NET <c>"N"</c> format);
    /// any other value is returned verbatim. Mirrors Capacitor's
    /// <c>EventStoreReadExtensions.NormalizeId</c>.
    /// </summary>
    public static string NormalizeId(string id) =>
        Guid.TryParse(id, out var guid) ? guid.ToString("N") : id;

    public static string AgentSession(string sessionId) =>
        $"{AgentSessionPrefix}{NormalizeId(sessionId)}";

    public static string AgentSubsession(string parentSessionId, string agentId) =>
        $"{AgentSubsessionPrefix}{NormalizeId(parentSessionId)}-{NormalizeId(agentId)}";

    public static string AgentMemory(string appName, string userId) =>
        $"{AgentMemoryPrefix}{NormalizeId(appName)}-{NormalizeId(userId)}";

    public static string AgentArtifact(string scope, string filename) =>
        $"{AgentArtifactPrefix}{NormalizeId(scope)}-{NormalizeId(filename)}";

    public static string EvalRun(string runId) =>
        $"{EvalRunPrefix}{NormalizeId(runId)}";
}
