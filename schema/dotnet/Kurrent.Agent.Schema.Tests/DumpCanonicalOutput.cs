using Google.Protobuf;
using Kurrent.Agent.Schema;
using Kurrent.Agent.Schema.Events;
using Xunit;

namespace Kurrent.Agent.Schema.Tests;

/// <summary>
/// Round-trip every fixture through SchemaJsonOptions.ToJson and write the
/// result under the directory named by the SCHEMA_DUMP_OUT environment
/// variable. CI structurally diffs this against the Python dumper output.
/// Skipped (no-op) when the env var is not set, so normal test runs are
/// unaffected.
/// </summary>
public class DumpCanonicalOutput {
    [Fact]
    public void Dump_canonical_output_when_env_set() {
        var outRoot = Environment.GetEnvironmentVariable("SCHEMA_DUMP_OUT");
        if (string.IsNullOrEmpty(outRoot)) return;

        var fixturesRoot = LocateFixtures();
        Directory.CreateDirectory(Path.Combine(outRoot, "events"));
        Directory.CreateDirectory(Path.Combine(outRoot, "metadata"));

        var fromJson = typeof(SchemaJsonOptions).GetMethod(nameof(SchemaJsonOptions.FromJson))!;

        foreach (var (eventName, clrType) in EventTypeMap.All) {
            var src = File.ReadAllText(Path.Combine(fixturesRoot, "events", $"{eventName}.json"));
            var parsed = (IMessage)fromJson.MakeGenericMethod(clrType).Invoke(null, new object[] { src })!;
            File.WriteAllText(Path.Combine(outRoot, "events", $"{eventName}.json"),
                              SchemaJsonOptions.ToJson(parsed));
        }

        var usageSrc = File.ReadAllText(Path.Combine(fixturesRoot, "metadata", "usage.json"));
        var usage = SchemaJsonOptions.FromJson<TokenUsage>(usageSrc);
        File.WriteAllText(Path.Combine(outRoot, "metadata", "usage.json"),
                          SchemaJsonOptions.ToJson(usage));
    }

    static string LocateFixtures() {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir is not null) {
            var candidate = Path.Combine(dir.FullName, "schema", "fixtures");
            if (Directory.Exists(candidate)) return candidate;
            candidate = Path.Combine(dir.FullName, "fixtures");
            if (Directory.Exists(candidate)
                && File.Exists(Path.Combine(dir.FullName, "SCHEMA_v2.md"))) return candidate;
            dir = dir.Parent;
        }
        throw new DirectoryNotFoundException("schema/fixtures not found");
    }
}
