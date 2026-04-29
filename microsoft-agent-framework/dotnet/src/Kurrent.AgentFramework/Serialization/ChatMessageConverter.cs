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
        var text    = message.Text;

        var responses = message.Contents.OfType<ToolApprovalResponseContent>().ToList();

        if (text is { Length: > 0 } t) {
            var evt = new UserMessageReceived {
                Content      = t,
                MessageIndex = messageIndex,
                Timestamp    = Timestamp.FromDateTimeOffset(timestamp),
            };
            if (msgId   is not null) evt.MessageId  = msgId;
            if (author  is not null) evt.AuthorName = author;
            if (created is { } c)    evt.CreatedAt  = Timestamp.FromDateTimeOffset(c);
            yield return evt;
        }

        foreach (var response in responses) {
            yield return BuildInterruptResolved(response, message, timestamp);
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
        // MAF's approval flow only fires for ApprovalRequiredAIFunction, which always
        // wraps a FunctionCallContent. If a future MAF version emits a different
        // ToolCallContent subtype here, fail loudly rather than silently emit a
        // schema-invalid event with an empty extensions.afw struct.
        var fc  = (FunctionCallContent)ta.ToolCall;
        var evt = new InterruptIssued {
            RequestId = fc.CallId ?? "",
            Kind      = "approval",
            Prompt    = BuildApprovalPrompt(fc),
            Timestamp = Timestamp.FromDateTimeOffset(ts),
        };
        if (!string.IsNullOrEmpty(fc.Name))  evt.ToolName  = fc.Name;
        if (carrier.MessageId is { } mid)    evt.MessageId = mid;
        evt.Extensions["afw"] = BuildAfwInterruptExtension(fc, ta.RequestId);
        return evt;
    }

    static InterruptResolved BuildInterruptResolved(ToolApprovalResponseContent tr, ChatMessage carrier, DateTimeOffset ts) {
        // Hard cast: MAF's approval flow always wraps a FunctionCallContent here. See BuildInterruptIssued for rationale.
        var fc  = (FunctionCallContent)tr.ToolCall;
        var evt = new InterruptResolved {
            RequestId = fc.CallId ?? "",
            Outcome   = tr.Approved ? "allow" : "deny",
            Timestamp = Timestamp.FromDateTimeOffset(ts),
        };
        if (carrier.MessageId is { } mid) evt.MessageId = mid;
        if (tr.Reason         is { } r)   evt.Response  = r;
        evt.Extensions["afw"] = BuildAfwInterruptExtension(fc, tr.RequestId);
        return evt;
    }

    /// <summary>
    /// Build a single <see cref="ChatMessage"/> from a group of events that share a <c>message_id</c>.
    /// Returns null when the group contains no chat-shaped events.
    /// </summary>
    public static ChatMessage? MergeIntoChatMessage(
        IReadOnlyList<object> events,
        IReadOnlyDictionary<string, InterruptIssued> issuedByRequestId) {

        if (events.Count == 0) return null;

        var role = DetermineRole(events);
        if (role is null) return null;

        var contents   = new List<AIContent>();
        string? msgId  = null;
        string? author = null;
        DateTimeOffset? created = null;

        foreach (var ev in events) {
            switch (ev) {
                case UserMessageReceived u:
                    if (u.HasContent && !string.IsNullOrEmpty(u.Content)) contents.Add(new TextContent(u.Content));
                    msgId   ??= u.HasMessageId  ? u.MessageId  : null;
                    author  ??= u.HasAuthorName ? u.AuthorName : null;
                    created ??= u.CreatedAt?.ToDateTimeOffset();
                    break;

                case AssistantTextGenerated at:
                    if (at.HasContent && !string.IsNullOrEmpty(at.Content)) contents.Add(new TextContent(at.Content));
                    msgId   ??= at.HasMessageId  ? at.MessageId  : null;
                    author  ??= at.HasAuthorName ? at.AuthorName : null;
                    created ??= at.CreatedAt?.ToDateTimeOffset();
                    break;

                case AssistantToolCallsGenerated ac:
                    if (ac.HasContent && !string.IsNullOrEmpty(ac.Content)) contents.Add(new TextContent(ac.Content));
                    contents.AddRange(
                        from tc in ac.ToolCalls
                        let args = StructToArguments(tc.Arguments)
                        select new FunctionCallContent(tc.CallId, tc.ToolName, args)
                    );
                    msgId   ??= ac.HasMessageId  ? ac.MessageId  : null;
                    author  ??= ac.HasAuthorName ? ac.AuthorName : null;
                    created ??= ac.CreatedAt?.ToDateTimeOffset();
                    break;

                case ToolResultReceived tr:
                    contents.Add(new FunctionResultContent(tr.CallId, tr.HasResult ? tr.Result : null));
                    msgId   ??= tr.HasMessageId  ? tr.MessageId  : null;
                    author  ??= tr.HasAuthorName ? tr.AuthorName : null;
                    created ??= tr.CreatedAt?.ToDateTimeOffset();
                    break;

                case InterruptIssued ii:
                    contents.Add(BuildApprovalRequestContent(ii));
                    msgId ??= ii.HasMessageId ? ii.MessageId : null;
                    break;

                case InterruptResolved ir:
                    if (BuildApprovalResponseContent(ir, issuedByRequestId) is { } far) contents.Add(far);
                    msgId ??= ir.HasMessageId ? ir.MessageId : null;
                    break;
            }
        }

        if (contents.Count == 0) return null;

        return new ChatMessage(role.Value, contents) {
            MessageId  = msgId,
            AuthorName = author,
            CreatedAt  = created,
        };
    }

    static ChatRole? DetermineRole(IReadOnlyList<object> events) {
        foreach (var ev in events) {
            switch (ev) {
                case UserMessageReceived:
                case InterruptResolved:
                    return ChatRole.User;
                case AssistantTextGenerated:
                case AssistantToolCallsGenerated:
                case InterruptIssued:
                    return ChatRole.Assistant;
                case ToolResultReceived:
                    return ChatRole.Tool;
            }
        }
        return null;
    }

    static ToolApprovalRequestContent BuildApprovalRequestContent(InterruptIssued ii) {
        var (fcCallId, fcName, fcArgs) = ReadProposedCall(ii.Extensions, ii.RequestId, ii.HasToolName ? ii.ToolName : null);
        var fc                          = new FunctionCallContent(fcCallId, fcName ?? "", fcArgs);
        var pairId                      = ReadApprovalPairId(ii.Extensions) ?? ii.RequestId;
        return new ToolApprovalRequestContent(pairId, fc);
    }

    static ToolApprovalResponseContent? BuildApprovalResponseContent(
        InterruptResolved ir,
        IReadOnlyDictionary<string, InterruptIssued> issuedByRequestId) {

        string? toolName;
        string callId;
        IDictionary<string, object?>? args;

        if (issuedByRequestId.TryGetValue(ir.RequestId, out var ii)) {
            (callId, toolName, args) = ReadProposedCall(ii.Extensions, ii.RequestId, ii.HasToolName ? ii.ToolName : null);
        } else if (TryReadProposedCall(ir.Extensions, ir.RequestId, out var c, out var n, out var a)) {
            callId   = c;
            toolName = n;
            args     = a;
        } else {
            // Pathological — no Issued, no proposed_call on the Resolved either. Skip.
            return null;
        }

        var fc     = new FunctionCallContent(callId, toolName ?? "", args);
        var pairId = ReadApprovalPairId(ir.Extensions) ?? ir.RequestId;
        var resp   = new ToolApprovalResponseContent(pairId, ir.Outcome == "allow", fc);
        if (ir.HasResponse) resp.Reason = ir.Response;
        return resp;
    }

    static (string CallId, string? Name, IDictionary<string, object?>? Args) ReadProposedCall(
        Google.Protobuf.Collections.MapField<string, Struct> extensions,
        string fallbackCallId,
        string? fallbackName) {
        return TryReadProposedCall(extensions, fallbackCallId, out var c, out var n, out var a)
            ? (c, n, a)
            : (fallbackCallId, fallbackName, null);
    }

    static bool TryReadProposedCall(
        Google.Protobuf.Collections.MapField<string, Struct> extensions,
        string fallbackCallId,
        out string callId,
        out string? toolName,
        out IDictionary<string, object?>? arguments) {

        callId    = fallbackCallId;
        toolName  = null;
        arguments = null;

        if (!extensions.TryGetValue("afw", out var afw)) return false;
        if (!afw.Fields.TryGetValue("interrupt", out var interruptValue)
            || interruptValue.KindCase != Value.KindOneofCase.StructValue) return false;
        var interrupt = interruptValue.StructValue;
        if (!interrupt.Fields.TryGetValue("proposed_call", out var proposedValue)
            || proposedValue.KindCase != Value.KindOneofCase.StructValue) return false;
        var proposed = proposedValue.StructValue;

        if (proposed.Fields.TryGetValue("id",   out var idVal)   && idVal.KindCase   == Value.KindOneofCase.StringValue) callId   = idVal.StringValue;
        if (proposed.Fields.TryGetValue("name", out var nameVal) && nameVal.KindCase == Value.KindOneofCase.StringValue) toolName = nameVal.StringValue;
        if (proposed.Fields.TryGetValue("arguments", out var argsVal) && argsVal.KindCase == Value.KindOneofCase.StructValue) {
            arguments = StructToArguments(argsVal.StructValue);
        }
        return true;
    }

    static string? ReadApprovalPairId(Google.Protobuf.Collections.MapField<string, Struct> extensions) {
        if (!extensions.TryGetValue("afw", out var afw)) return null;
        if (!afw.Fields.TryGetValue("interrupt", out var interruptValue)
            || interruptValue.KindCase != Value.KindOneofCase.StructValue) return null;
        var interrupt = interruptValue.StructValue;
        return interrupt.Fields.TryGetValue("approval_pair_id", out var v)
            && v.KindCase == Value.KindOneofCase.StringValue
                ? v.StringValue
                : null;
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
