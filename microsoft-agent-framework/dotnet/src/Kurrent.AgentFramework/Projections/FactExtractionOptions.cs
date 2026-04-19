using KurrentDB.Client;

namespace Kurrent.AgentFramework.Projections;

/// <summary>
/// Options for <see cref="FactExtractionService"/>.
/// </summary>
/// <param name="GroupName">
/// Persistent subscription group name. The server retains acked-position state per group,
/// so the same group name survives restarts without reprocessing. Pick a unique name per
/// deployment of the service (e.g. when running multiple fact extractors side by side).
/// </param>
/// <param name="StartFrom">
/// Log position at which the group begins when first created. Ignored on subsequent restarts
/// — the server resumes from the last acked position. Defaults to <see cref="Position.Start"/>,
/// which backfills all pre-existing <c>AgentSession-</c> events on first deployment.
/// </param>
public sealed record FactExtractionOptions(
    string     GroupName = "FactExtraction",
    IPosition? StartFrom = null
);
