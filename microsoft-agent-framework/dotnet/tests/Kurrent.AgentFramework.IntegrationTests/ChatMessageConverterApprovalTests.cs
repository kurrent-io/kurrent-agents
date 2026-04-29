using Google.Protobuf.WellKnownTypes;
using Kurrent.AgentFramework.Serialization;
using Microsoft.Extensions.AI;

namespace Kurrent.AgentFramework.IntegrationTests;

public class ChatMessageConverterApprovalTests {
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
}
