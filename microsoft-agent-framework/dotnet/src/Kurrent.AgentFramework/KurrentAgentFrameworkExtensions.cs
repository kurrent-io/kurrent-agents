using Kurrent.AgentFramework.Memory;
using Kurrent.AgentFramework.Projections;
using KurrentDB.Client;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;


namespace Kurrent.AgentFramework;

public static class KurrentAgentFrameworkExtensions {
    extension(IServiceCollection services) {
        /// <summary>
        /// Registers KurrentDB clients (standard + persistent subscriptions) from configuration.
        /// Reads "KurrentDB:ConnectionString" from config.
        /// </summary>
        public IServiceCollection AddKurrentAgentFramework(IConfiguration configuration) {
            var connectionString = configuration["KurrentDb:ConnectionString"]
             ?? "kurrentdb://localhost:2113?tls=false";

            var settings = KurrentDBClientSettings.Create(connectionString);
            services.AddSingleton(new KurrentDBClient(settings));
            services.AddSingleton(new KurrentDBPersistentSubscriptionsClient(settings));

            return services;
        }

        /// <summary>
        /// Registers agent memory with the default KurrentDB-backed implementation.
        /// Facts are stored in <c>AgentMemory-{appName}-{userId}</c> (canonical per
        /// <c>SCHEMA_v2.md §2.1</c>). To use an alternative backend, register your
        /// <see cref="IAgentMemory"/> before calling this method, or use that
        /// backend's dedicated extension.
        /// </summary>
        /// <param name="appName">
        /// Application identifier — the canonical per-tenant scope. Required unless
        /// <paramref name="streamName"/> is set.
        /// </param>
        /// <param name="userId">
        /// User identifier — the canonical per-tenant scope. Required unless
        /// <paramref name="streamName"/> is set.
        /// </param>
        /// <param name="factExtractor">
        /// Optional pluggable fact extraction logic. Receives a user message, returns zero or
        /// more facts to retain. If null, no automatic fact extraction runs (facts can still
        /// be retained explicitly via a RetainFact tool).
        /// </param>
        /// <param name="factExtractionOptions">
        /// Optional tuning for the fact-extraction persistent subscription (group name, start
        /// position). Ignored when <paramref name="factExtractor"/> is null.
        /// </param>
        /// <param name="streamName">
        /// Optional explicit stream override — bypasses the canonical builder for a
        /// deliberately shared cross-tenant stream or a custom scope. When set,
        /// <paramref name="appName"/> and <paramref name="userId"/> are not needed.
        /// </param>
        public IServiceCollection AddKurrentAgentMemory(
            string?                appName                = null,
            string?                userId                 = null,
            FactExtractor?         factExtractor          = null,
            FactExtractionOptions? factExtractionOptions  = null,
            string?                streamName             = null
        ) {
            services.TryAddSingleton<IAgentMemory>(sp => new KurrentDBAgentMemory(
                sp.GetRequiredService<KurrentDBClient>(),
                appName,
                userId,
                streamName
            ));
            services.TryAddSingleton<AgentMemoryContextProvider>();

            if (factExtractor is not null) {
                services.TryAddSingleton(factExtractor);

                // Caller-provided options are authoritative; only fall back to defaults
                // when the caller did not supply any and none were registered earlier.
                if (factExtractionOptions is not null)
                    services.Replace(ServiceDescriptor.Singleton(factExtractionOptions));
                else
                    services.TryAddSingleton(new FactExtractionOptions());

                // AddHostedService uses TryAddEnumerable internally, so it is idempotent
                // for the same concrete service type.
                services.AddHostedService<FactExtractionService>();
            }

            return services;
        }

    }
}
