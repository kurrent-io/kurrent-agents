using Microsoft.Extensions.AI;

namespace Kurrent.AgentFramework.Capture;

/// <summary>
/// Captures ChatResponse usage data from IChatClient middleware
/// and makes it available to the ChatHistoryProvider during store.
/// Usage is keyed by message ID for correct correlation in tool-calling loops.
/// Not thread-safe — both capture and read happen sequentially within a single RunAsync.
/// </summary>
public sealed class UsageCapture {
    readonly Dictionary<string, UsageDetails> _usages = new();

    public bool TryGet(string messageId, out UsageDetails? usage) => _usages.TryGetValue(messageId, out usage);

    public void Clear() => _usages.Clear();

    /// <summary>
    /// Wraps an IChatClient to capture usage keyed by response message IDs.
    /// Supports both non-streaming and streaming paths.
    /// </summary>
    public IChatClient Wrap(IChatClient inner) =>
        new ChatClientBuilder(inner)
            .Use(
                getResponseFunc: async (messages, options, innerClient, ct) => {
                    var response = await innerClient.GetResponseAsync(messages, options, ct).ConfigureAwait(false);
                    CaptureFromResponse(response);

                    return response;
                },
                getStreamingResponseFunc: (messages, options, innerClient, ct) => {
                    var stream = innerClient.GetStreamingResponseAsync(messages, options, ct);

                    return CaptureFromStreamAsync(stream);
                }
            )
            .Build();

    void CaptureFromResponse(ChatResponse response) {
        if (response.Usage is not { } usage) return;

        foreach (var msg in response.Messages) {
            if (msg.MessageId is not null)
                _usages[msg.MessageId] = usage;
        }
    }

    async IAsyncEnumerable<ChatResponseUpdate> CaptureFromStreamAsync(IAsyncEnumerable<ChatResponseUpdate> stream) {
        UsageDetails? lastUsage     = null;
        string?       lastMessageId = null;

        await foreach (var update in stream.ConfigureAwait(false)) {
            // Capture message ID from updates
            if (update.MessageId is not null)
                lastMessageId = update.MessageId;

            // Capture usage from UsageContent items in streaming updates
            foreach (var content in update.Contents.OfType<UsageContent>()) {
                lastUsage = content.Details;
            }

            yield return update;
        }

        // After stream completes, associate captured usage with the message ID
        if (lastUsage is not null && lastMessageId is not null)
            _usages[lastMessageId] = lastUsage;
    }
}
