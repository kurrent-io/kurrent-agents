using DotNet.Testcontainers.Builders;
using KurrentDB.Client;
using Testcontainers.KurrentDb;
using TUnit.Core.Interfaces;

namespace Kurrent.AgentFramework.IntegrationTests;

/// <summary>
/// Shared KurrentDB container used by all integration tests in this project.
/// Registered via <c>[ClassDataSource&lt;KurrentDbFixture&gt;(Shared = SharedType.PerTestSession)]</c>.
/// </summary>
public sealed class KurrentDbFixture : IAsyncInitializer, IAsyncDisposable {
    KurrentDbContainer _container = null!;

    public string ConnectionString { get; private set; } = null!;

    public async Task InitializeAsync() {
        // Match the image used by docker-compose.yml at the repo root — it has the
        // secondary-index feature (e.g. $idx-ce-AgentSession) that the OTEL projection
        // relies on. KURRENTDB_INSECURE=true disables both TLS and auth.
        _container = new KurrentDbBuilder("kurrentplatform/kurrentdb:26.0.2-experimental-arm64-10.0-noble")
            .WithEnvironment("KURRENTDB_CLUSTER_SIZE",              "1")
            .WithEnvironment("KURRENTDB_RUN_PROJECTIONS",           "None")
            .WithEnvironment("KURRENTDB_ENABLE_ATOM_PUB_OVER_HTTP", "true")
            .WithEnvironment("KURRENTDB_INSECURE",                  "true")
            .WithWaitStrategy(
                Wait.ForUnixContainer()
                    .UntilHttpRequestIsSucceeded(
                        x => x.ForPath("/gossip").ForPort(2113),
                        x => x.WithTimeout(TimeSpan.FromMinutes(2))
                    )
            )
            .Build();

        await _container.StartAsync();
        ConnectionString = _container.GetConnectionString();
    }

    public KurrentDBClientSettings ClientSettings() => KurrentDBClientSettings.Create(ConnectionString);

    public KurrentDBClient CreateClient() => new(ClientSettings());

    public KurrentDBPersistentSubscriptionsClient CreatePersistentClient() => new(ClientSettings());

    public async ValueTask DisposeAsync() {
        GC.Collect();
        GC.WaitForPendingFinalizers();
        await _container.DisposeAsync();
        GC.Collect();
        GC.WaitForPendingFinalizers();
    }
}
