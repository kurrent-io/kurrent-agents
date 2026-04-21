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
        // KURRENTDB_INSECURE=true disables both TLS and auth. Image tag is picked
        // per host arch because the kurrentplatform/kurrentdb experimental track
        // does not publish a multi-arch manifest list; set KURRENTDB_IMAGE to
        // override in CI or for custom registries.
        _container = new KurrentDbBuilder(KurrentDbImage.Resolve())
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
