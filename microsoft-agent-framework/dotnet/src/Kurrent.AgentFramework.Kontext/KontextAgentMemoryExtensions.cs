using Kurrent.AgentFramework.Memory;
using Kurrent.AgentFramework.Projections;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;

namespace Kurrent.AgentFramework.Kontext;

public static class KontextAgentMemoryExtensions {
    extension(IServiceCollection services) {
        /// <summary>
        /// Registers agent memory backed by Kurrent.Kontext (hybrid BM25 + vector search).
        /// Call after <c>AddKontext</c> or <c>AddKontextWithExternalClient</c>.
        /// </summary>
        /// <param name="factExtractor">
        /// Optional fact extraction logic. See <see cref="FactExtractor"/>.
        /// </param>
        /// <param name="factExtractionOptions">
        /// Optional tuning for the fact-extraction persistent subscription (group name, start
        /// position). Ignored when <paramref name="factExtractor"/> is null.
        /// </param>
        public IServiceCollection AddKontextAgentMemory(
            FactExtractor?         factExtractor         = null,
            FactExtractionOptions? factExtractionOptions = null
        ) {
            services.Replace(ServiceDescriptor.Singleton<IAgentMemory, KontextAgentMemory>());
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
