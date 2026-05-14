using Xunit;

namespace Kurrent.Agent.Schema.Tests;

public class StreamNamesTests {
    [Fact]
    public void NormalizeId_hyphenated_guid_returns_dashless_lowercase() {
        const string hyphenated = "8D77FD28-FDA0-485F-9AE1-8EE9C7FC3751";

        var normalized = StreamNames.NormalizeId(hyphenated);

        Assert.Equal("8d77fd28fda0485f9ae18ee9c7fc3751", normalized);
    }

    [Fact]
    public void NormalizeId_dashless_guid_returns_unchanged() {
        const string dashless = "8d77fd28fda0485f9ae18ee9c7fc3751";

        var normalized = StreamNames.NormalizeId(dashless);

        Assert.Equal(dashless, normalized);
    }

    [Fact]
    public void NormalizeId_uppercase_dashless_guid_returns_lowercase() {
        const string upper = "8D77FD28FDA0485F9AE18EE9C7FC3751";

        var normalized = StreamNames.NormalizeId(upper);

        Assert.Equal("8d77fd28fda0485f9ae18ee9c7fc3751", normalized);
    }

    [Fact]
    public void NormalizeId_braced_guid_returns_dashless_lowercase() {
        const string braced = "{8d77fd28-fda0-485f-9ae1-8ee9c7fc3751}";

        var normalized = StreamNames.NormalizeId(braced);

        Assert.Equal("8d77fd28fda0485f9ae18ee9c7fc3751", normalized);
    }

    [Fact]
    public void NormalizeId_non_guid_returns_verbatim() {
        const string opaque = "my-app-user_42.session";

        var normalized = StreamNames.NormalizeId(opaque);

        Assert.Equal(opaque, normalized);
    }

    [Fact]
    public void NormalizeId_empty_returns_verbatim() {
        var normalized = StreamNames.NormalizeId("");

        Assert.Equal("", normalized);
    }

    [Fact]
    public void AgentSession_does_not_normalize_internally() {
        // Builders are intentionally pass-through — callers are responsible for
        // normalising id components per SCHEMA_v2.md §2.4 (typically by composing
        // StreamNames.AgentSession(StreamNames.NormalizeId(sessionId))). This test
        // pins that contract so the symmetry with Python's streams.py is not
        // broken silently.
        var streamName = StreamNames.AgentSession("8d77fd28-fda0-485f-9ae1-8ee9c7fc3751");

        Assert.Equal("AgentSession-8d77fd28-fda0-485f-9ae1-8ee9c7fc3751", streamName);
    }
}
