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
}
