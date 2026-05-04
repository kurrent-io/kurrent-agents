using System.Text.Json;
using Google.Protobuf.WellKnownTypes;
using Kurrent.Agent.Schema.Events;
using Kurrent.AgentFramework.Serialization;
using Microsoft.Extensions.AI;

namespace Kurrent.AgentFramework.IntegrationTests;

public class ChatMessageConverterTests {
    static readonly DateTimeOffset Ts  = new(2026, 4, 17, 12, 0, 0, TimeSpan.Zero);
    static readonly Timestamp      Pts = Timestamp.FromDateTimeOffset(Ts);

    [Test]
    public async Task ToEvents_UserMessage_EmitsSingleUserMessageReceived() {
        var msg = new ChatMessage(ChatRole.User, "hello") {
            MessageId  = "m1",
            AuthorName = "daisy",
            CreatedAt  = Ts,
        };

        var events = ChatMessageConverter.ToEvents(msg, messageIndex: 3, timestamp: Ts).ToList();

        await Assert.That(events).HasSingleItem();
        var user = await Assert.That(events[0]).IsTypeOf<UserMessageReceived>();
        await Assert.That(user!.Content).IsEqualTo("hello");
        await Assert.That(user.MessageId).IsEqualTo("m1");
        await Assert.That(user.AuthorName).IsEqualTo("daisy");
        await Assert.That(user.MessageIndex).IsEqualTo(3);
        await Assert.That(user.Timestamp).IsEqualTo(Pts);
    }

    [Test]
    public async Task ToEvents_AssistantText_EmitsAssistantTextGenerated() {
        var msg = new ChatMessage(ChatRole.Assistant, "hi there");

        var events = ChatMessageConverter.ToEvents(msg, messageIndex: 0, timestamp: Ts).ToList();

        await Assert.That(events).HasSingleItem();
        await Assert.That(events[0]).IsTypeOf<AssistantTextGenerated>();
    }

    [Test]
    public async Task ToEvents_AssistantWithFunctionCall_EmitsToolCallsEvent() {
        var args = new Dictionary<string, object?> { ["city"] = "Paris" };

        var msg = new ChatMessage(
            ChatRole.Assistant,
            [
                new TextContent("looking it up"),
                new FunctionCallContent("call-1", "get_weather", args),
            ]
        );

        var events = ChatMessageConverter.ToEvents(msg, messageIndex: 2, timestamp: Ts).ToList();

        await Assert.That(events).HasSingleItem();
        var toolCalls = await Assert.That(events[0]).IsTypeOf<AssistantToolCallsGenerated>();
        await Assert.That(toolCalls!.Content).IsEqualTo("looking it up");
        await Assert.That(toolCalls.ToolCalls.Count).IsEqualTo(1);
        await Assert.That(toolCalls.ToolCalls[0].CallId).IsEqualTo("call-1");
        await Assert.That(toolCalls.ToolCalls[0].ToolName).IsEqualTo("get_weather");
        await Assert.That(toolCalls.ToolCalls[0].Arguments).IsNotNull();
        await Assert.That(toolCalls.ToolCalls[0].Arguments!.Fields["city"].StringValue).IsEqualTo("Paris");
    }

    [Test]
    public async Task ToEvents_ToolResult_EmitsOneToolResultPerFunctionResultContent() {
        var msg = new ChatMessage(
            ChatRole.Tool,
            [
                new FunctionResultContent("call-1", "sunny"),
                new FunctionResultContent("call-2", "72F"),
            ]
        );

        var events = ChatMessageConverter.ToEvents(msg, messageIndex: 4, timestamp: Ts).ToList();

        await Assert.That(events.Count).IsEqualTo(2);
        var first = await Assert.That(events[0]).IsTypeOf<ToolResultReceived>();
        await Assert.That(first!.CallId).IsEqualTo("call-1");
        await Assert.That(first.Result).IsEqualTo("sunny");
        var second = (ToolResultReceived)events[1];
        await Assert.That(second.CallId).IsEqualTo("call-2");
    }

    [Test]
    public async Task ToEvents_SystemMessage_EmitsNothing() {
        // Current behavior: only User/Assistant/Tool roles produce events.
        var msg = new ChatMessage(ChatRole.System, "be concise");

        var events = ChatMessageConverter.ToEvents(msg, messageIndex: 0, timestamp: Ts).ToList();

        await Assert.That(events).IsEmpty();
    }

    [Test]
    public async Task ToChatMessage_UserEvent_RoundTripsRoleAndContent() {
        var e = new UserMessageReceived {
            Content      = "hello",
            MessageId    = "m1",
            AuthorName   = "daisy",
            CreatedAt    = Pts,
            MessageIndex = 0,
            Timestamp    = Pts,
        };

        var msg = ChatMessageConverter.ToChatMessage(e);

        await Assert.That(msg).IsNotNull();
        await Assert.That(msg!.Role).IsEqualTo(ChatRole.User);
        await Assert.That(msg.Text).IsEqualTo("hello");
        await Assert.That(msg.MessageId).IsEqualTo("m1");
        await Assert.That(msg.AuthorName).IsEqualTo("daisy");
    }

    [Test]
    public async Task ToChatMessage_AssistantToolCalls_ReconstructsFunctionCallContent() {
        var args = ChatMessageConverter.JsonElementToStruct(
            JsonSerializer.SerializeToElement(new Dictionary<string, object?> { ["city"] = "Paris" })
        );

        var e = new AssistantToolCallsGenerated {
            Content      = "looking",
            MessageIndex = 0,
            Timestamp    = Pts,
        };
        e.ToolCalls.Add(new ToolCallInfo { CallId = "call-1", ToolName = "get_weather", Arguments = args });

        var msg = ChatMessageConverter.ToChatMessage(e);

        await Assert.That(msg).IsNotNull();
        await Assert.That(msg!.Role).IsEqualTo(ChatRole.Assistant);
        var call = msg.Contents.OfType<FunctionCallContent>().Single();
        await Assert.That(call.CallId).IsEqualTo("call-1");
        await Assert.That(call.Name).IsEqualTo("get_weather");
        await Assert.That(call.Arguments).IsNotNull();
        await Assert.That(call.Arguments!["city"]?.ToString()).IsEqualTo("Paris");
    }

    [Test]
    public async Task ToChatMessage_ToolResult_ReconstructsFunctionResultContent() {
        var e = new ToolResultReceived {
            CallId       = "call-1",
            ToolName     = "get_weather",
            Result       = "sunny",
            MessageIndex = 0,
            Timestamp    = Pts,
        };

        var msg = ChatMessageConverter.ToChatMessage(e);

        await Assert.That(msg).IsNotNull();
        await Assert.That(msg!.Role).IsEqualTo(ChatRole.Tool);
        var result = msg.Contents.OfType<FunctionResultContent>().Single();
        await Assert.That(result.CallId).IsEqualTo("call-1");
        await Assert.That(result.Result?.ToString()).IsEqualTo("sunny");
    }

    [Test]
    public async Task ToChatMessage_NonChatEvent_ReturnsNull() {
        var e = new SessionStarted { AgentName = "a", Model = "m", Timestamp = Pts };

        await Assert.That(ChatMessageConverter.ToChatMessage(e)).IsNull();
    }
}
