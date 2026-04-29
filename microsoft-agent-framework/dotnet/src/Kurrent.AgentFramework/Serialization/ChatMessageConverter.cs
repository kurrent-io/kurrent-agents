using System.Text.Json;
using Google.Protobuf.WellKnownTypes;
using Kurrent.Agent.Schema.Events;
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
        if (message.Role == ChatRole.User)      return ToUserEvents(message, messageIndex, timestamp);
        if (message.Role == ChatRole.Assistant) return ToAssistantEvents(message, messageIndex, timestamp);
        if (message.Role == ChatRole.Tool)      return ToToolEvents(message, messageIndex, timestamp);
        return [];
    }

    static IEnumerable<object> ToUserEvents(ChatMessage message, int messageIndex, DateTimeOffset timestamp) {
        var msgId   = message.MessageId;
        var author  = message.AuthorName;
        var created = message.CreatedAt;

        if (message.Text is { Length: > 0 } text) {
            var evt = new UserMessageReceived {
                Content      = text,
                MessageIndex = messageIndex,
                Timestamp    = Timestamp.FromDateTimeOffset(timestamp),
            };
            if (msgId   is not null) evt.MessageId  = msgId;
            if (author  is not null) evt.AuthorName = author;
            if (created is { } c)    evt.CreatedAt  = Timestamp.FromDateTimeOffset(c);
            yield return evt;
        }
    }

    static IEnumerable<object> ToAssistantEvents(ChatMessage message, int messageIndex, DateTimeOffset timestamp) {
        var msgId   = message.MessageId;
        var author  = message.AuthorName;
        var created = message.CreatedAt;
        var text    = message.Text;

        var functionCalls = message.Contents.OfType<FunctionCallContent>().ToList();
        var approvals     = message.Contents.OfType<ToolApprovalRequestContent>().ToList();

        if (functionCalls.Count > 0) {
            var evt = new AssistantToolCallsGenerated {
                MessageIndex = messageIndex,
                Timestamp    = Timestamp.FromDateTimeOffset(timestamp),
            };
            evt.ToolCalls.AddRange(functionCalls.Select(BuildToolCallInfo));
            if (text    is { Length: > 0 } t) evt.Content    = t;
            if (msgId   is not null)          evt.MessageId  = msgId;
            if (author  is not null)          evt.AuthorName = author;
            if (created is { } c)             evt.CreatedAt  = Timestamp.FromDateTimeOffset(c);
            yield return evt;
        } else if (text is { Length: > 0 } t) {
            var evt = new AssistantTextGenerated {
                Content      = t,
                MessageIndex = messageIndex,
                Timestamp    = Timestamp.FromDateTimeOffset(timestamp),
            };
            if (msgId   is not null) evt.MessageId  = msgId;
            if (author  is not null) evt.AuthorName = author;
            if (created is { } c)    evt.CreatedAt  = Timestamp.FromDateTimeOffset(c);
            yield return evt;
        }

        foreach (var approval in approvals) {
            yield return BuildInterruptIssued(approval, message, timestamp);
        }
    }

    static IEnumerable<object> ToToolEvents(ChatMessage message, int messageIndex, DateTimeOffset timestamp) {
        var msgId   = message.MessageId;
        var author  = message.AuthorName;
        var created = message.CreatedAt;

        foreach (var result in message.Contents.OfType<FunctionResultContent>()) {
            var evt = new ToolResultReceived {
                CallId       = result.CallId ?? "",
                MessageIndex = messageIndex,
                Timestamp    = Timestamp.FromDateTimeOffset(timestamp),
            };
            if (result.Result?.ToString() is { } r) evt.Result     = r;
            if (msgId   is not null)                evt.MessageId  = msgId;
            if (author  is not null)                evt.AuthorName = author;
            if (created is { } c)                   evt.CreatedAt  = Timestamp.FromDateTimeOffset(c);
            yield return evt;
        }
    }

    static InterruptIssued BuildInterruptIssued(ToolApprovalRequestContent ta, ChatMessage carrier, DateTimeOffset ts) {
        var tc  = ta.ToolCall;
        var fc  = tc as FunctionCallContent;
        var evt = new InterruptIssued {
            RequestId = tc.CallId ?? "",
            Kind      = "approval",
            Prompt    = fc is not null ? BuildApprovalPrompt(fc) : $"Approve calling {tc.CallId}?",
            Timestamp = Timestamp.FromDateTimeOffset(ts),
        };
        if (fc is not null && !string.IsNullOrEmpty(fc.Name)) evt.ToolName  = fc.Name;
        if (carrier.MessageId is { } mid)                     evt.MessageId = mid;
        evt.Extensions["afw"] = fc is not null
            ? BuildAfwInterruptExtension(fc, ta.RequestId)
            : new Struct();
        return evt;
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
        new(ChatRole.User, e.HasContent ? e.Content : null) {
            MessageId  = e.HasMessageId  ? e.MessageId  : null,
            AuthorName = e.HasAuthorName ? e.AuthorName : null,
            CreatedAt  = e.CreatedAt?.ToDateTimeOffset(),
        };

    static ChatMessage FromAssistantText(AssistantTextGenerated e) =>
        new(ChatRole.Assistant, e.HasContent ? e.Content : null) {
            MessageId  = e.HasMessageId  ? e.MessageId  : null,
            AuthorName = e.HasAuthorName ? e.AuthorName : null,
            CreatedAt  = e.CreatedAt?.ToDateTimeOffset(),
        };

    static ChatMessage FromAssistantToolCalls(AssistantToolCallsGenerated e) {
        var contents = new List<AIContent>();

        if (e.HasContent && !string.IsNullOrEmpty(e.Content)) {
            contents.Add(new TextContent(e.Content));
        }

        contents.AddRange(
            from tc in e.ToolCalls
            let args = StructToArguments(tc.Arguments)
            select new FunctionCallContent(tc.CallId, tc.ToolName, args)
        );

        return new(ChatRole.Assistant, contents) {
            MessageId  = e.HasMessageId  ? e.MessageId  : null,
            AuthorName = e.HasAuthorName ? e.AuthorName : null,
            CreatedAt  = e.CreatedAt?.ToDateTimeOffset(),
        };
    }

    static ChatMessage FromToolResult(ToolResultReceived e) {
        var result = new FunctionResultContent(e.CallId, e.HasResult ? e.Result : null);

        return new(ChatRole.Tool, [result]) {
            MessageId  = e.HasMessageId  ? e.MessageId  : null,
            AuthorName = e.HasAuthorName ? e.AuthorName : null,
            CreatedAt  = e.CreatedAt?.ToDateTimeOffset(),
        };
    }

    static ToolCallInfo BuildToolCallInfo(FunctionCallContent fc) {
        var info = new ToolCallInfo {
            CallId   = fc.CallId ?? "",
            ToolName = fc.Name   ?? "",
        };
        if (fc.Arguments is { Count: > 0 }) {
            // Free-form JSON arguments fold into the canonical Struct shape.
            info.Arguments = JsonElementToStruct(JsonSerializer.SerializeToElement(fc.Arguments));
        }
        return info;
    }

    static IDictionary<string, object?>? StructToArguments(Struct? args) {
        if (args is null || args.Fields.Count == 0) return null;

        return args.Fields.ToDictionary(
            kv => kv.Key,
            kv => (object?)ValueToObject(kv.Value)
        );
    }

    static object? ValueToObject(Value value) => value.KindCase switch {
        Value.KindOneofCase.NullValue   => null,
        Value.KindOneofCase.BoolValue   => value.BoolValue,
        Value.KindOneofCase.NumberValue => value.NumberValue,
        Value.KindOneofCase.StringValue => value.StringValue,
        Value.KindOneofCase.StructValue => value.StructValue.Fields
            .ToDictionary(kv => kv.Key, kv => (object?)ValueToObject(kv.Value)),
        Value.KindOneofCase.ListValue   => value.ListValue.Values
            .Select(ValueToObject).ToList(),
        _ => null,
    };

    internal static Struct JsonElementToStruct(JsonElement element) {
        var s = new Struct();
        if (element.ValueKind != JsonValueKind.Object) return s;
        foreach (var prop in element.EnumerateObject())
            s.Fields[prop.Name] = JsonElementToValue(prop.Value);
        return s;
    }

    static Value JsonElementToValue(JsonElement element) => element.ValueKind switch {
        JsonValueKind.Object => Value.ForStruct(JsonElementToStruct(element)),
        JsonValueKind.Array  => Value.ForList(element.EnumerateArray().Select(JsonElementToValue).ToArray()),
        JsonValueKind.String => Value.ForString(element.GetString() ?? ""),
        JsonValueKind.Number => Value.ForNumber(element.GetDouble()),
        JsonValueKind.True   => Value.ForBool(true),
        JsonValueKind.False  => Value.ForBool(false),
        _                    => Value.ForNull(),
    };

    internal static Struct BuildAfwInterruptExtension(FunctionCallContent fc, string? approvalPairId) {
        var proposed = new Struct();
        proposed.Fields["id"]   = Value.ForString(fc.CallId ?? "");
        proposed.Fields["name"] = Value.ForString(fc.Name   ?? "");
        if (fc.Arguments is { Count: > 0 }) {
            proposed.Fields["arguments"] = Value.ForStruct(JsonElementToStruct(JsonSerializer.SerializeToElement(fc.Arguments)));
        } else {
            proposed.Fields["arguments"] = Value.ForStruct(new Struct());
        }

        var interrupt = new Struct();
        interrupt.Fields["proposed_call"] = Value.ForStruct(proposed);
        if (!string.IsNullOrEmpty(approvalPairId) && approvalPairId != fc.CallId) {
            interrupt.Fields["approval_pair_id"] = Value.ForString(approvalPairId);
        }

        var afw = new Struct();
        afw.Fields["interrupt"] = Value.ForStruct(interrupt);
        return afw;
    }

    internal const int ApprovalPromptMaxLength = 200;

    internal static string BuildApprovalPrompt(FunctionCallContent fc) {
        var name = fc.Name ?? "";
        var head = $"Approve calling {name}";

        var withoutArgs = $"{head}?";
        if (fc.Arguments is not { Count: > 0 }) {
            if (withoutArgs.Length > ApprovalPromptMaxLength) return withoutArgs[..ApprovalPromptMaxLength];
            return withoutArgs;
        }

        var argsRendered = string.Join(", ", fc.Arguments.Select(kv =>
            $"{kv.Key}={JsonSerializer.Serialize(kv.Value)}"));

        var full = $"{head}({argsRendered})?";
        if (full.Length <= ApprovalPromptMaxLength) return full;

        if (withoutArgs.Length >= ApprovalPromptMaxLength) return withoutArgs[..ApprovalPromptMaxLength];

        // Truncate args, append … then close.
        var available = ApprovalPromptMaxLength - $"{head}(…)?".Length;
        if (available <= 0) return withoutArgs;
        return $"{head}({argsRendered[..available]}…)?";
    }
}
