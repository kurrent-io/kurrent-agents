using System.Runtime.InteropServices;

namespace Kurrent.AgentFramework.IntegrationTests;

/// <summary>
/// Resolves the KurrentDB image tag to use on this host. The
/// <c>kurrentplatform/kurrentdb</c> experimental track does not publish a
/// multi-arch manifest list — separate tags exist per architecture. Callers
/// must either match the host CPU or set <c>KURRENTDB_IMAGE</c> explicitly
/// (CI, custom registry, pinned build).
/// </summary>
public static class KurrentDbImage {
    public const string EnvVar = "KURRENTDB_IMAGE";

    // Single source of truth for per-arch tags. Bumping the KurrentDB version
    // is a single edit here.
    static readonly IReadOnlyDictionary<Architecture, string> Images =
        new Dictionary<Architecture, string> {
            [Architecture.Arm64] = "kurrentplatform/kurrentdb:26.0.2-experimental-arm64-10.0-noble",
            [Architecture.X64]   = "kurrentplatform/kurrentdb:26.0.2",
        };

    /// <summary>
    /// Resolution order: <c>KURRENTDB_IMAGE</c> env var, then arch-based lookup,
    /// then hard fail. No silent default — pulling the wrong-arch image is a
    /// confusing failure mode we'd rather surface up front.
    /// </summary>
    public static string Resolve() => Resolve(RuntimeInformation.OSArchitecture, Environment.GetEnvironmentVariable(EnvVar));

    // Overload for unit tests — injects arch + env value without touching process state.
    internal static string Resolve(Architecture arch, string? envOverride) {
        if (!string.IsNullOrEmpty(envOverride)) return envOverride;
        if (Images.TryGetValue(arch, out var tag)) return tag;
        throw new PlatformNotSupportedException($"Unsupported CPU arch for KurrentDB image: {arch}. Set {EnvVar} to override.");
    }

    internal static IReadOnlyDictionary<Architecture, string> Tags => Images;
}
