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
        /// To use an alternative backend (e.g. Kurrent.Kontext for hybrid search),
        /// register your <see cref="IAgentMemory"/> before calling this method, or
        /// use that backend's dedicated extension.
        /// </summary>
        /// <param name="factExtractor">
        /// Optional pluggable fact extraction logic. Receives a user message, returns zero or
        /// more facts to retain. If null, no automatic fact extraction runs (facts can still
        /// be retained explicitly via a RetainFact tool).
        /// </param>
        /// <param name="factExtractionOptions">
        /// Optional tuning for the fact-extraction persistent subscription (group name, start
        /// position). Ignored when <paramref name="factExtractor"/> is null.
        /// </param>
        public IServiceCollection AddKurrentAgentMemory(
            FactExtractor?         factExtractor          = null,
            FactExtractionOptions? factExtractionOptions  = null
        ) {
            services.TryAddSingleton<IAgentMemory, KurrentDBAgentMemory>();
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
