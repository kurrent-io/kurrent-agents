using KurrentDB.Client;
using Microsoft.Agents.AI.Workflows;

namespace Kurrent.AgentFramework.Workflows;

/// <summary>
/// Factory for creating CheckpointManager instances backed by KurrentDB.
/// </summary>
public static class KurrentDBCheckpointManagerFactory {
    /// <summary>
    /// Creates a CheckpointManager that stores workflow checkpoints as events in KurrentDB.
    /// Each checkpoint is an event in a WorkflowCheckpoint-{sessionId} stream.
    /// </summary>
    public static CheckpointManager Create(KurrentDBClient client) =>
        CheckpointManager.CreateJson(new KurrentDBCheckpointStore(client));
}
