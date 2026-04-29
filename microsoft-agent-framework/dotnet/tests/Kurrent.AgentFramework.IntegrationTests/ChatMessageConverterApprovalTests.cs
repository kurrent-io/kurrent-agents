using Google.Protobuf.WellKnownTypes;
using Kurrent.Agent.Schema.Events;
using Kurrent.AgentFramework.Serialization;
using Microsoft.Extensions.AI;

namespace Kurrent.AgentFramework.IntegrationTests;

public class ChatMessageConverterApprovalTests {
    static readonly DateTimeOffset Ts = new(2026, 4, 29, 12, 0, 0, TimeSpan.Zero);

    [Test]
    public async Task BuildApprovalPrompt_SimpleArgs_RendersArgpacked() {
        var fc = new FunctionCallContent("call-1", "send_email",
            new Dictionary<string, object?> { ["to"] = "alice@x.com", ["subject"] = "hi" });

        var prompt = ChatMessageConverter.BuildApprovalPrompt(fc);

        await Assert.That(prompt).IsEqualTo("Approve calling send_email(to=\"alice@x.com\", subject=\"hi\")?");
    }

    [Test]
    public async Task BuildApprovalPrompt_NoArgs_OmitsParentheses() {
        var fc = new FunctionCallContent("call-1", "ping", arguments: null);

        var prompt = ChatMessageConverter.BuildApprovalPrompt(fc);

        await Assert.That(prompt).IsEqualTo("Approve calling ping?");
    }

    [Test]
    public async Task BuildApprovalPrompt_LongArgs_TruncatesWithEllipsis() {
        var bigValue = new string('x', 500);
        var fc       = new FunctionCallContent("call-1", "huge",
            new Dictionary<string, object?> { ["payload"] = bigValue });

        var prompt = ChatMessageConverter.BuildApprovalPrompt(fc);

        await Assert.That(prompt.Length).IsEqualTo(ChatMessageConverter.ApprovalPromptMaxLength);
        await Assert.That(prompt.EndsWith("…)?")).IsTrue();
        await Assert.That(prompt.StartsWith("Approve calling huge(payload=\"")).IsTrue();
    }

    [Test]
    public async Task BuildApprovalPrompt_NameLongerThanCap_HardCapsFallback() {
        var name = new string('n', 250);
        var fc   = new FunctionCallContent("call-1", name, arguments: null);

        var prompt = ChatMessageConverter.BuildApprovalPrompt(fc);

        await Assert.That(prompt.Length).IsEqualTo(ChatMessageConverter.ApprovalPromptMaxLength);
        await Assert.That(prompt.StartsWith("Approve calling n")).IsTrue();
    }

    [Test]
    public async Task BuildAfwInterruptExtension_CallIdEqualsPairId_OmitsPairId() {
        var fc = new FunctionCallContent("call-1", "ping",
            new Dictionary<string, object?> { ["x"] = 1 });

        var ext = ChatMessageConverter.BuildAfwInterruptExtension(fc, approvalPairId: "call-1");

        var interrupt = ext.Fields["interrupt"].StructValue;
        await Assert.That(interrupt.Fields.ContainsKey("approval_pair_id")).IsFalse();

        var proposed = interrupt.Fields["proposed_call"].StructValue;
        await Assert.That(proposed.Fields["id"].StringValue).IsEqualTo("call-1");
        await Assert.That(proposed.Fields["name"].StringValue).IsEqualTo("ping");
        await Assert.That(proposed.Fields["arguments"].StructValue.Fields["x"].NumberValue).IsEqualTo(1);
    }

    [Test]
    public async Task BuildAfwInterruptExtension_DifferingPairId_IncludesPairId() {
        var fc = new FunctionCallContent("call-1", "ping", arguments: null);

        var ext = ChatMessageConverter.BuildAfwInterruptExtension(fc, approvalPairId: "approval-pair-9");

        var interrupt = ext.Fields["interrupt"].StructValue;
        await Assert.That(interrupt.Fields["approval_pair_id"].StringValue).IsEqualTo("approval-pair-9");

        var proposed = interrupt.Fields["proposed_call"].StructValue;
        await Assert.That(proposed.Fields["id"].StringValue).IsEqualTo("call-1");
        await Assert.That(proposed.Fields["name"].StringValue).IsEqualTo("ping");
        await Assert.That(proposed.Fields["arguments"].StructValue.Fields.Count).IsEqualTo(0);
    }

    [Test]
    public async Task ToEvents_AssistantTextAndApproval_EmitsTextAndInterrupt() {
        var fc = new FunctionCallContent("call-1", "send_email",
            new Dictionary<string, object?> { ["to"] = "alice" });
        var msg = new ChatMessage(ChatRole.Assistant, [
            new TextContent("Drafting an email — needs your approval."),
            new ToolApprovalRequestContent(requestId: "call-1", toolCall: fc),
        ]) { MessageId = "asst-msg-1" };

        var events = ChatMessageConverter.ToEvents(msg, messageIndex: 0, timestamp: Ts).ToList();

        await Assert.That(events.Count).IsEqualTo(2);

        var text = await Assert.That(events[0]).IsTypeOf<AssistantTextGenerated>();
        await Assert.That(text!.Content).IsEqualTo("Drafting an email — needs your approval.");
        await Assert.That(text.MessageId).IsEqualTo("asst-msg-1");

        var ii = await Assert.That(events[1]).IsTypeOf<InterruptIssued>();
        await Assert.That(ii!.RequestId).IsEqualTo("call-1");
        await Assert.That(ii.Kind).IsEqualTo("approval");
        await Assert.That(ii.ToolName).IsEqualTo("send_email");
        await Assert.That(ii.MessageId).IsEqualTo("asst-msg-1");
        await Assert.That(ii.Prompt.StartsWith("Approve calling send_email(")).IsTrue();
        await Assert.That(ii.Extensions["afw"].Fields["interrupt"].StructValue
            .Fields["proposed_call"].StructValue.Fields["name"].StringValue).IsEqualTo("send_email");
    }

    [Test]
    public async Task ToEvents_AssistantApprovalOnly_SuppressesTextEvent() {
        var fc  = new FunctionCallContent("call-1", "ping", arguments: null);
        var msg = new ChatMessage(ChatRole.Assistant, [
            new ToolApprovalRequestContent(requestId: "call-1", toolCall: fc),
        ]) { MessageId = "asst-msg-2" };

        var events = ChatMessageConverter.ToEvents(msg, messageIndex: 0, timestamp: Ts).ToList();

        await Assert.That(events).HasSingleItem();
        await Assert.That(events[0]).IsTypeOf<InterruptIssued>();
    }

    [Test]
    public async Task ToEvents_AssistantMixedToolsAndApprovals_BothEmitted() {
        var fc       = new FunctionCallContent("call-1", "lookup", arguments: null);
        var approval = new ToolApprovalRequestContent(requestId: "call-2",
            toolCall: new FunctionCallContent("call-2", "delete", arguments: null));
        var msg = new ChatMessage(ChatRole.Assistant, [
            new TextContent("Looking up; will need approval to delete."),
            fc,
            approval,
        ]) { MessageId = "asst-msg-3" };

        var events = ChatMessageConverter.ToEvents(msg, messageIndex: 0, timestamp: Ts).ToList();

        await Assert.That(events.Count).IsEqualTo(2);
        var tools = await Assert.That(events[0]).IsTypeOf<AssistantToolCallsGenerated>();
        await Assert.That(tools!.ToolCalls.Count).IsEqualTo(1);
        await Assert.That(tools.ToolCalls[0].CallId).IsEqualTo("call-1");

        var ii = await Assert.That(events[1]).IsTypeOf<InterruptIssued>();
        await Assert.That(ii!.RequestId).IsEqualTo("call-2");
    }

    [Test]
    public async Task ToEvents_ApprovalPairIdDifferent_StashedInExtensions() {
        var fc  = new FunctionCallContent("call-1", "ping", arguments: null);
        var msg = new ChatMessage(ChatRole.Assistant, [
            new ToolApprovalRequestContent(requestId: "approval-pair-9", toolCall: fc),
        ]) { MessageId = "asst-msg-4" };

        var events = ChatMessageConverter.ToEvents(msg, messageIndex: 0, timestamp: Ts).ToList();

        var ii = (InterruptIssued)events.Single();
        var interrupt = ii.Extensions["afw"].Fields["interrupt"].StructValue;
        await Assert.That(interrupt.Fields["approval_pair_id"].StringValue).IsEqualTo("approval-pair-9");
    }

    [Test]
    public async Task ToEvents_UserApprovalResponse_EmitsInterruptResolved() {
        var fc       = new FunctionCallContent("call-1", "send_email",
            new Dictionary<string, object?> { ["to"] = "alice" });
        var response = new ToolApprovalResponseContent(requestId: "call-1", approved: true, toolCall: fc) {
            Reason = "Looks good.",
        };
        var msg = new ChatMessage(ChatRole.User, [response]) { MessageId = "user-msg-1" };

        var events = ChatMessageConverter.ToEvents(msg, messageIndex: 1, timestamp: Ts).ToList();

        await Assert.That(events).HasSingleItem();
        var ir = await Assert.That(events[0]).IsTypeOf<InterruptResolved>();
        await Assert.That(ir!.RequestId).IsEqualTo("call-1");
        await Assert.That(ir.Outcome).IsEqualTo("allow");
        await Assert.That(ir.Response).IsEqualTo("Looks good.");
        await Assert.That(ir.MessageId).IsEqualTo("user-msg-1");
        await Assert.That(ir.Extensions["afw"].Fields["interrupt"].StructValue
            .Fields["proposed_call"].StructValue.Fields["name"].StringValue).IsEqualTo("send_email");
    }

    [Test]
    public async Task ToEvents_UserApprovalResponseDenied_EmitsDenyOutcome() {
        var fc       = new FunctionCallContent("call-1", "send_email", arguments: null);
        var response = new ToolApprovalResponseContent(requestId: "call-1", approved: false, toolCall: fc);
        var msg      = new ChatMessage(ChatRole.User, [response]);

        var events = ChatMessageConverter.ToEvents(msg, messageIndex: 0, timestamp: Ts).ToList();

        var ir = (InterruptResolved)events.Single();
        await Assert.That(ir.Outcome).IsEqualTo("deny");
        await Assert.That(ir.HasResponse).IsFalse();
    }

    [Test]
    public async Task ToEvents_UserApprovalResponseWithText_EmitsBothEvents() {
        var fc       = new FunctionCallContent("call-1", "ping", arguments: null);
        var response = new ToolApprovalResponseContent(requestId: "call-1", approved: true, toolCall: fc);
        var msg      = new ChatMessage(ChatRole.User, [
            new TextContent("OK go ahead."),
            response,
        ]) { MessageId = "user-msg-2" };

        var events = ChatMessageConverter.ToEvents(msg, messageIndex: 0, timestamp: Ts).ToList();

        await Assert.That(events.Count).IsEqualTo(2);
        await Assert.That(events[0]).IsTypeOf<UserMessageReceived>();
        await Assert.That(events[1]).IsTypeOf<InterruptResolved>();
    }

    [Test]
    public async Task MergeIntoChatMessage_AssistantTextAndInterrupt_RebuildsContents() {
        var fc       = new FunctionCallContent("call-1", "send_email",
            new Dictionary<string, object?> { ["to"] = "alice" });
        var original = new ChatMessage(ChatRole.Assistant, [
            new TextContent("Drafting…"),
            new ToolApprovalRequestContent(requestId: "call-1", toolCall: fc),
        ]) { MessageId = "asst-msg-1" };

        var events = ChatMessageConverter.ToEvents(original, messageIndex: 0, timestamp: Ts).ToList();
        var issued = events.OfType<InterruptIssued>().ToDictionary(e => e.RequestId);

        var rebuilt = ChatMessageConverter.MergeIntoChatMessage(events, issued);

        await Assert.That(rebuilt).IsNotNull();
        await Assert.That(rebuilt!.Role).IsEqualTo(ChatRole.Assistant);
        await Assert.That(rebuilt.MessageId).IsEqualTo("asst-msg-1");
        await Assert.That(rebuilt.Contents.OfType<TextContent>().Single().Text).IsEqualTo("Drafting…");
        var fa = rebuilt.Contents.OfType<ToolApprovalRequestContent>().Single();
        await Assert.That(fa.RequestId).IsEqualTo("call-1");
        var rebuiltFc = (FunctionCallContent)fa.ToolCall;
        await Assert.That(rebuiltFc.CallId).IsEqualTo("call-1");
        await Assert.That(rebuiltFc.Name).IsEqualTo("send_email");
        await Assert.That(rebuiltFc.Arguments!["to"]?.ToString()).IsEqualTo("alice");
    }

    [Test]
    public async Task MergeIntoChatMessage_UserResponse_RebuildsContentsViaCrossEventLookup() {
        var fc           = new FunctionCallContent("call-1", "send_email",
            new Dictionary<string, object?> { ["to"] = "alice" });
        var assistantMsg = new ChatMessage(ChatRole.Assistant, [
            new ToolApprovalRequestContent(requestId: "call-1", toolCall: fc),
        ]) { MessageId = "asst-msg-1" };
        var userMsg = new ChatMessage(ChatRole.User, [
            new ToolApprovalResponseContent(requestId: "call-1", approved: true, toolCall: fc),
        ]) { MessageId = "user-msg-2" };

        var assistantEvents = ChatMessageConverter.ToEvents(assistantMsg, 0, Ts).ToList();
        var userEvents      = ChatMessageConverter.ToEvents(userMsg, 1, Ts).ToList();
        var issued          = assistantEvents.OfType<InterruptIssued>().ToDictionary(e => e.RequestId);

        var rebuilt = ChatMessageConverter.MergeIntoChatMessage(userEvents, issued);

        await Assert.That(rebuilt).IsNotNull();
        await Assert.That(rebuilt!.Role).IsEqualTo(ChatRole.User);
        var resp = rebuilt.Contents.OfType<ToolApprovalResponseContent>().Single();
        await Assert.That(resp.Approved).IsTrue();
        var rebuiltFc = (FunctionCallContent)resp.ToolCall;
        await Assert.That(rebuiltFc.Name).IsEqualTo("send_email");
        await Assert.That(rebuiltFc.Arguments!["to"]?.ToString()).IsEqualTo("alice");
    }

    [Test]
    public async Task MergeIntoChatMessage_ResolvedWithoutIssued_FallsBackToOwnProposedCall() {
        var fc       = new FunctionCallContent("call-1", "send_email", arguments: null);
        var response = new ToolApprovalResponseContent(requestId: "call-1", approved: false, toolCall: fc);
        var userMsg  = new ChatMessage(ChatRole.User, [response]) { MessageId = "user-msg-2" };

        var userEvents = ChatMessageConverter.ToEvents(userMsg, 0, Ts).ToList();
        var emptyIndex = new Dictionary<string, InterruptIssued>();

        var rebuilt = ChatMessageConverter.MergeIntoChatMessage(userEvents, emptyIndex);

        await Assert.That(rebuilt).IsNotNull();
        var resp = rebuilt!.Contents.OfType<ToolApprovalResponseContent>().Single();
        await Assert.That(resp.Approved).IsFalse();
        var rebuiltFc = (FunctionCallContent)resp.ToolCall;
        await Assert.That(rebuiltFc.CallId).IsEqualTo("call-1");
        await Assert.That(rebuiltFc.Name).IsEqualTo("send_email");
    }
}
