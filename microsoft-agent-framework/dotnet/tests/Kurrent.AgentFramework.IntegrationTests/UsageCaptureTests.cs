using System.Runtime.CompilerServices;
using Kurrent.AgentFramework.Capture;
using Microsoft.Extensions.AI;

namespace Kurrent.AgentFramework.IntegrationTests;

/// <summary>
/// <see cref="UsageCapture"/> is middleware over <see cref="IChatClient"/>; these tests
/// drive it with stub clients (no container needed) and assert the message-id → usage map.
/// </summary>
public class UsageCaptureTests {
    sealed class StubChatClient(
            ChatResponse?                         response = null,
            IAsyncEnumerable<ChatResponseUpdate>? stream   = null
        ) : IChatClient {
        public Task<ChatResponse> GetResponseAsync(
                IEnumerable<ChatMessage> messages,
                ChatOptions?             options           = null,
                CancellationToken        cancellationToken = default
            ) => Task.FromResult(response ?? new ChatResponse());

        public IAsyncEnumerable<ChatResponseUpdate> GetStreamingResponseAsync(
                IEnumerable<ChatMessage> messages,
                ChatOptions?             options           = null,
                CancellationToken        cancellationToken = default
            ) => stream ?? AsyncEnumerable.Empty<ChatResponseUpdate>();

        public object? GetService(Type serviceType, object? serviceKey = null) => null;
        public void Dispose() { }
    }

    static class AsyncEnumerable {
#pragma warning disable CS1998
        public static async IAsyncEnumerable<T> Empty<T>() { yield break; }

        public static async IAsyncEnumerable<T> From<T>(
                IEnumerable<T>                             items,
                [EnumeratorCancellation] CancellationToken ct = default
            ) {
            foreach (var item in items) {
                ct.ThrowIfCancellationRequested();

                yield return item;
            }
        }
#pragma warning restore CS1998
    }

    [Test]
    public async Task NonStreaming_AttachesUsageToEveryMessageId() {
        var usage = new UsageDetails { InputTokenCount = 10, OutputTokenCount = 20, TotalTokenCount = 30 };

        var response = new ChatResponse(
            [
                new(ChatRole.Assistant, "first") { MessageId  = "msg-1" },
                new(ChatRole.Assistant, "second") { MessageId = "msg-2" },
            ]
        ) { Usage = usage };

        var capture = new UsageCapture();
        var wrapped = capture.Wrap(new StubChatClient(response));

        await wrapped.GetResponseAsync([new(ChatRole.User, "go")]);

        await Assert.That(capture.TryGet("msg-1", out var a)).IsTrue();
        await Assert.That(a!.InputTokenCount).IsEqualTo(10L);
        await Assert.That(capture.TryGet("msg-2", out var b)).IsTrue();
        await Assert.That(b!.OutputTokenCount).IsEqualTo(20L);
    }

    [Test]
    public async Task NonStreaming_WithoutUsage_RecordsNothing() {
        var response = new ChatResponse([new(ChatRole.Assistant, "hi") { MessageId = "msg-x" }]);

        var capture = new UsageCapture();
        var wrapped = capture.Wrap(new StubChatClient(response));

        await wrapped.GetResponseAsync([new ChatMessage(ChatRole.User, "go")]);

        await Assert.That(capture.TryGet("msg-x", out _)).IsFalse();
    }

    [Test]
    public async Task NonStreaming_SkipsMessagesWithoutMessageId() {
        var usage = new UsageDetails { InputTokenCount = 5 };

        var response = new ChatResponse(
            [
                new(ChatRole.Assistant, "unnamed"), // no MessageId
                new(ChatRole.Assistant, "named") { MessageId = "msg-1" },
            ]
        ) { Usage = usage };

        var capture = new UsageCapture();
        var wrapped = capture.Wrap(new StubChatClient(response));

        await wrapped.GetResponseAsync([new(ChatRole.User, "go")]);

        await Assert.That(capture.TryGet("msg-1", out var u)).IsTrue();
        await Assert.That(u!.InputTokenCount).IsEqualTo(5L);
    }

    [Test]
    public async Task Streaming_AttachesLastUsageToLastMessageId() {
        var usage = new UsageDetails { InputTokenCount = 7, OutputTokenCount = 3 };

        var updates = new[] {
            new ChatResponseUpdate(ChatRole.Assistant, "hel") { MessageId = "msg-1" },
            new ChatResponseUpdate(ChatRole.Assistant, "lo") { MessageId  = "msg-1" },
            // Usage typically arrives in a final update
            new ChatResponseUpdate(ChatRole.Assistant, (string?)null) {
                MessageId = "msg-1",
                Contents  = [new UsageContent(usage)],
            },
        };

        var capture = new UsageCapture();
        var wrapped = capture.Wrap(new StubChatClient(stream: AsyncEnumerable.From(updates)));

        await foreach (var _ in wrapped.GetStreamingResponseAsync([new ChatMessage(ChatRole.User, "hi")])) { }

        await Assert.That(capture.TryGet("msg-1", out var u)).IsTrue();
        await Assert.That(u!.InputTokenCount).IsEqualTo(7L);
        await Assert.That(u.OutputTokenCount).IsEqualTo(3L);
    }

    [Test]
    public async Task Streaming_WithoutUsage_RecordsNothing() {
        var updates = new[] {
            new ChatResponseUpdate(ChatRole.Assistant, "hel") { MessageId = "msg-1" },
            new ChatResponseUpdate(ChatRole.Assistant, "lo") { MessageId  = "msg-1" },
        };

        var capture = new UsageCapture();
        var wrapped = capture.Wrap(new StubChatClient(stream: AsyncEnumerable.From(updates)));

        await foreach (var _ in wrapped.GetStreamingResponseAsync([new(ChatRole.User, "hi")])) { }

        await Assert.That(capture.TryGet("msg-1", out _)).IsFalse();
    }

    [Test]
    public async Task Clear_EmptiesTheCapturedUsageMap() {
        var usage = new UsageDetails { InputTokenCount = 1 };

        var response = new ChatResponse([new(ChatRole.Assistant, "x") { MessageId = "msg-1" }]) { Usage = usage };

        var capture = new UsageCapture();
        var wrapped = capture.Wrap(new StubChatClient(response));
        await wrapped.GetResponseAsync([new(ChatRole.User, "go")]);

        await Assert.That(capture.TryGet("msg-1", out _)).IsTrue();

        capture.Clear();

        await Assert.That(capture.TryGet("msg-1", out _)).IsFalse();
    }

    [Test]
    public async Task Streaming_ForwardsAllUpdatesToCaller() {
        var updates = new[] {
            new ChatResponseUpdate(ChatRole.Assistant, "one") { MessageId   = "msg-1" },
            new ChatResponseUpdate(ChatRole.Assistant, "two") { MessageId   = "msg-1" },
            new ChatResponseUpdate(ChatRole.Assistant, "three") { MessageId = "msg-1" },
        };

        var capture = new UsageCapture();
        var wrapped = capture.Wrap(new StubChatClient(stream: AsyncEnumerable.From(updates)));

        var seen = new List<string>();

        await foreach (var u in wrapped.GetStreamingResponseAsync([new(ChatRole.User, "go")])) {
            seen.Add(u.Text ?? "");
        }

        await Assert.That(seen.Count).IsEqualTo(3);
        await Assert.That(seen).IsEquivalentTo(["one", "two", "three"]);
    }
}
