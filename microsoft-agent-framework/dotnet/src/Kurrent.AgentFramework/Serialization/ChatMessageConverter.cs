using System.Text.Json;
using Kurrent.AgentFramework.Events;
using Microsoft.Extensions.AI;

namespace Kurrent.AgentFramework.Serialization;

/// <summary>
/// Converts between Microsoft.Extensions.AI.ChatMessage and KurrentDB events.
/// Decomposes messages into rich typed events on write, reconstructs on read.
/// </summary>
public static class ChatMessageConverter {
    /// <summary>
    /// Decompose a ChatMessage into one or more typed domain events.
    /// </summary>
    public static IEnumerable<object> ToEvents(ChatMessage message, int messageIndex, DateTimeOffset timestamp) {
        var msgId   = message.MessageId;
        var author  = message.AuthorName;
        var created = message.CreatedAt;

        if (message.Role == ChatRole.User) {
            yield return new UserMessageReceived(
                Content: message.Text,
                MessageId: msgId,
                AuthorName: author,
                CreatedAt: created,
                MessageIndex: messageIndex,
                Timestamp: timestamp
            );

            yield break;
        }

        if (message.Role == ChatRole.Assistant) {
            var functionCalls = message.Contents
                .OfType<FunctionCallContent>()
                .ToList();

            if (functionCalls.Count > 0) {
                yield return new AssistantToolCallsGenerated(
                    ToolCalls: functionCalls.Select(fc => new ToolCallInfo(
                                CallId: fc.CallId ?? "",
                                ToolName: fc.Name ?? "",
                                Arguments: fc.Arguments is not null
                                    ? JsonSerializer.SerializeToElement(fc.Arguments)
                                    : null
                            )
                        )
                        .ToList(),
                    Content: message.Text,
                    MessageId: msgId,
                    AuthorName: author,
                    CreatedAt: created,
                    MessageIndex: messageIndex,
                    Timestamp: timestamp
                );
            } else {
                yield return new AssistantTextGenerated(
                    Content: message.Text,
                    MessageId: msgId,
                    AuthorName: author,
                    CreatedAt: created,
                    MessageIndex: messageIndex,
                    Timestamp: timestamp
                );
            }

            yield break;
        }

        if (message.Role == ChatRole.Tool) {
            foreach (var result in message.Contents.OfType<FunctionResultContent>()) {
                yield return new ToolResultReceived(
                    CallId: result.CallId ?? "",
                    ToolName: null,
                    Result: result.Result?.ToString(),
                    MessageId: msgId,
                    AuthorName: author,
                    CreatedAt: created,
                    MessageIndex: messageIndex,
                    Timestamp: timestamp
                );
            }
        }
    }

    /// <summary>
    /// Reconstruct a ChatMessage from a domain event.
    /// Returns null for events that don't map to a ChatMessage (e.g. session lifecycle).
    /// </summary>
    public static ChatMessage? ToChatMessage(object @event) => @event switch {
        UserMessageReceived e         => FromUser(e),
        AssistantTextGenerated e      => FromAssistantText(e),
        AssistantToolCallsGenerated e => FromAssistantToolCalls(e),
        ToolResultReceived e          => FromToolResult(e),
        _                             => null
    };

    static ChatMessage FromUser(UserMessageReceived e) =>
        new(ChatRole.User, e.Content) {
            MessageId  = e.MessageId,
            AuthorName = e.AuthorName,
            CreatedAt  = e.CreatedAt,
        };

    static ChatMessage FromAssistantText(AssistantTextGenerated e) =>
        new(ChatRole.Assistant, e.Content) {
            MessageId  = e.MessageId,
            AuthorName = e.AuthorName,
            CreatedAt  = e.CreatedAt,
        };

    static ChatMessage FromAssistantToolCalls(AssistantToolCallsGenerated e) {
        var contents = new List<AIContent>();

        if (!string.IsNullOrEmpty(e.Content)) {
            contents.Add(new TextContent(e.Content));
        }

        contents.AddRange(
            (from tc in e.ToolCalls
             let args = tc.Arguments is { ValueKind: not JsonValueKind.Undefined and not JsonValueKind.Null }
                 ? tc.Arguments.Value.Deserialize<IDictionary<string, object?>>()
                 : null
             select new FunctionCallContent(tc.CallId, tc.ToolName, args)).Cast<AIContent>()
        );

        return new(ChatRole.Assistant, contents) {
            MessageId  = e.MessageId,
            AuthorName = e.AuthorName,
            CreatedAt  = e.CreatedAt,
        };
    }

    static ChatMessage FromToolResult(ToolResultReceived e) {
        var result = new FunctionResultContent(e.CallId, e.Result);

        return new(ChatRole.Tool, [result]) {
            MessageId  = e.MessageId,
            AuthorName = e.AuthorName,
            CreatedAt  = e.CreatedAt,
        };
    }
}
