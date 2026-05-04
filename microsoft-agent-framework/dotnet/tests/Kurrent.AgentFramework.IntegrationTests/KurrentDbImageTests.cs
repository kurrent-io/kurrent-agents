using System.Runtime.InteropServices;

namespace Kurrent.AgentFramework.IntegrationTests;

public class KurrentDbImageTests {
    [Test]
    public async Task EnvOverride_WinsOverArch() {
        var resolved = KurrentDbImage.Resolve(Architecture.X64, "example.com/kurrentdb:override");

        await Assert.That(resolved).IsEqualTo("example.com/kurrentdb:override");
    }

    [Test]
    public async Task Arm64_ReturnsArm64Tag() {
        var resolved = KurrentDbImage.Resolve(Architecture.Arm64, envOverride: null);

        await Assert.That(resolved).IsEqualTo(KurrentDbImage.Tags[Architecture.Arm64]);
    }

    [Test]
    public async Task X64_ReturnsX64Tag() {
        var resolved = KurrentDbImage.Resolve(Architecture.X64, envOverride: null);

        await Assert.That(resolved).IsEqualTo(KurrentDbImage.Tags[Architecture.X64]);
    }

    [Test]
    public async Task EmptyEnvOverride_FallsBackToArch() {
        // GetEnvironmentVariable can return "" for an unset-but-seen var on some shells;
        // treat it the same as null so we don't pass an empty string to Testcontainers.
        var resolved = KurrentDbImage.Resolve(Architecture.Arm64, envOverride: "");

        await Assert.That(resolved).IsEqualTo(KurrentDbImage.Tags[Architecture.Arm64]);
    }

    [Test]
    public async Task UnsupportedArch_ThrowsWithEnvVarHint() {
        await Assert.That(() => KurrentDbImage.Resolve(Architecture.RiscV64, envOverride: null))
            .ThrowsExactly<PlatformNotSupportedException>()
            .WithMessageContaining("KURRENTDB_IMAGE");
    }

    [Test]
    public async Task TagTable_CoversBothSupportedArches() {
        await Assert.That(KurrentDbImage.Tags.Keys).IsEquivalentTo(new[] { Architecture.Arm64, Architecture.X64 });

        foreach (var tag in KurrentDbImage.Tags.Values) {
            await Assert.That(tag).StartsWith("kurrentplatform/kurrentdb:");
        }
    }
}
