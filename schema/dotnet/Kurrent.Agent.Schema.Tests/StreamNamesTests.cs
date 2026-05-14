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
    public void AgentSession_normalizes_hyphenated_guid_to_dashless_stream() {
        // Builders apply NormalizeId so every .NET producer and reader converges
        // on the canonical dashless form for GUID-shaped components without each
        // call site having to remember. Non-GUID values pass through verbatim
        // because NormalizeId is idempotent on non-Guids.
        var streamName = StreamNames.AgentSession("8d77fd28-fda0-485f-9ae1-8ee9c7fc3751");

        Assert.Equal("AgentSession-8d77fd28fda0485f9ae18ee9c7fc3751", streamName);
    }

    [Fact]
    public void AgentSession_passes_through_non_guid_session_id() {
        var streamName = StreamNames.AgentSession("user-supplied-session");

        Assert.Equal("AgentSession-user-supplied-session", streamName);
    }

    [Fact]
    public void AgentSubsession_normalizes_both_components() {
        var streamName = StreamNames.AgentSubsession(
            "8d77fd28-fda0-485f-9ae1-8ee9c7fc3751",
            "1A2B3C4D-5E6F-7A8B-9C0D-1E2F3A4B5C6D"
        );

        Assert.Equal(
            "AgentSubsession-8d77fd28fda0485f9ae18ee9c7fc3751-1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d",
            streamName
        );
    }

    [Fact]
    public void AgentSubsession_preserves_opaque_agent_id() {
        // Producer-chosen ids like "research-x9k2" don't parse as Guid and stay verbatim.
        var streamName = StreamNames.AgentSubsession(
            "8d77fd28-fda0-485f-9ae1-8ee9c7fc3751",
            "research-x9k2"
        );

        Assert.Equal("AgentSubsession-8d77fd28fda0485f9ae18ee9c7fc3751-research-x9k2", streamName);
    }
}
