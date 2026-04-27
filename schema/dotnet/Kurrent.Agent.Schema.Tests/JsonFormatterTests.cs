using Google.Protobuf;
using Kurrent.Agent.Schema;
using Kurrent.Agent.Schema.Events;
using Xunit;

namespace Kurrent.Agent.Schema.Tests;

public class JsonFormatterTests {
    [Fact]
    public void ToJson_uses_snake_case() {
        var msg = new SessionStarted { AppName = "my-app" };
        var json = SchemaJsonOptions.ToJson(msg);
        Assert.Contains("\"app_name\"", json);
        Assert.DoesNotContain("\"appName\"", json);
    }

    [Fact]
    public void FromJson_round_trips() {
        const string src = "{\"app_name\":\"x\",\"timestamp\":\"2026-01-01T00:00:00Z\"}";
        var msg = SchemaJsonOptions.FromJson<SessionStarted>(src);
        Assert.Equal("x", msg.AppName);
    }
}
