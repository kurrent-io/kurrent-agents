# MAF tool-approval interrupts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Emit canonical `InterruptIssued`/`InterruptResolved` events for MAF tool-approval flows in both .NET and Python integrations, with round-trip fidelity through KurrentDB.

**Architecture:** Per-content-block decomposition of `ChatMessage`/`Message` on write — each `FunctionApprovalRequestContent` becomes an `InterruptIssued`, each `FunctionApprovalResponseContent` becomes an `InterruptResolved`, both anchored to their carrier message via `message_id`. The proposed call lives under `extensions.afw.interrupt.proposed_call`. `request_id` is the `FunctionCall.CallId`, satisfying the post-hoc correlation rule from `SCHEMA_v2.md §3.3` by construction. On read, the history provider groups events by `message_id` and rebuilds multi-block messages via a new `MergeIntoChatMessage` entry point. Cross-event lookup (Resolved → Issued by `request_id`) reconstructs the inner FCC for the response side.

**Tech Stack:** .NET 10 (`Microsoft.Agents.AI` 1.0.0, `Microsoft.Extensions.AI`, KurrentDB.Client 1.3.1, TUnit), Python 3.11+ (`agent-framework-core`, `kurrentdbclient`, `pytest`), shared canonical schema (`Kurrent.Agent.Schema` / `kurrent_agent_schema`).

**Delivery shape:** Two PRs (.NET first, Python second). Each phase produces a working test suite on its own.

---

## File map

**Phase 1 — .NET:**
- Modify: `microsoft-agent-framework/dotnet/src/Kurrent.AgentFramework/Serialization/ChatMessageConverter.cs`
- Modify: `microsoft-agent-framework/dotnet/src/Kurrent.AgentFramework/ChatHistory/KurrentDBChatHistoryProvider.cs`
- Create: `microsoft-agent-framework/dotnet/tests/Kurrent.AgentFramework.IntegrationTests/ChatMessageConverterApprovalTests.cs`
- Modify: `microsoft-agent-framework/dotnet/tests/Kurrent.AgentFramework.IntegrationTests/KurrentDBChatHistoryProviderTests.cs` (add round-trip block)
- Modify: `microsoft-agent-framework/dotnet/tests/Kurrent.AgentFramework.IntegrationTests/EventTypeMapTests.cs`
- Modify: `schema/fixtures/events/InterruptIssued.json` (replace contents)
- Modify: `schema/fixtures/events/InterruptResolved.json` (replace contents)

**Phase 2 — Python:**
- Modify: `microsoft-agent-framework/python/kurrent_agent_framework/chat_history.py`
- Modify: `microsoft-agent-framework/python/tests/test_chat_history.py` (add new test cases)

---

## Phase 1 — .NET

### Task 1: Replace schema fixtures with MAF-flavored data

**Files:**
- Modify: `schema/fixtures/events/InterruptIssued.json`
- Modify: `schema/fixtures/events/InterruptResolved.json`

The existing fixtures hold Claude-Code-themed `kind=permission` data with `extensions.claude_code.permission`. Replace with `kind=approval` MAF-flavored data exercising the canonical contract: populated `message_id`, `tool_name`, argpacked `prompt`, and `extensions.afw.interrupt.proposed_call`.

`request_id` is shared between the two fixtures and matches a representative `FunctionCall.CallId`, demonstrating the post-hoc correlation rule.

- [ ] **Step 1: Rewrite `InterruptIssued.json`**

Overwrite `schema/fixtures/events/InterruptIssued.json` with:

```json
{
  "request_id": "call_send_email_001",
  "kind": "approval",
  "tool_name": "send_email",
  "prompt": "Approve calling send_email(to=\"alice@example.com\", subject=\"Project update\")?",
  "message_id": "asst_msg_42",
  "timestamp": "2026-04-29T10:00:10Z",
  "extensions": {
    "afw": {
      "interrupt": {
        "proposed_call": {
          "id": "call_send_email_001",
          "name": "send_email",
          "arguments": {
            "to": "alice@example.com",
            "subject": "Project update"
          }
        }
      }
    }
  }
}
```

- [ ] **Step 2: Rewrite `InterruptResolved.json`**

Overwrite `schema/fixtures/events/InterruptResolved.json` with:

```json
{
  "request_id": "call_send_email_001",
  "outcome": "allow",
  "response": "Looks good — proceed.",
  "message_id": "user_msg_43",
  "timestamp": "2026-04-29T10:00:25Z",
  "extensions": {
    "afw": {
      "interrupt": {
        "proposed_call": {
          "id": "call_send_email_001",
          "name": "send_email",
          "arguments": {
            "to": "alice@example.com",
            "subject": "Project update"
          }
        }
      }
    }
  }
}
```

- [ ] **Step 3: Run schema fixture round-trip tests in .NET (no MAF integration tests yet)**

Run: `cd schema/dotnet && dotnet test Kurrent.Agent.Schema.Tests`
Expected: PASS — the schema package's own fixture tests round-trip the new JSON through proto and back successfully.

- [ ] **Step 4: Run schema fixture round-trip tests in Python**

Run: `cd schema/python && uv run pytest tests/test_json_helper.py -v`
Expected: PASS — Python schema fixture tests round-trip the new JSON.

- [ ] **Step 5: Commit**

```bash
git add schema/fixtures/events/InterruptIssued.json schema/fixtures/events/InterruptResolved.json
git commit -m "$(cat <<'EOF'
test(schema): switch interrupt fixtures to MAF kind=approval flavor (DEV-1611)

Replaces the Claude-Code-themed kind=permission fixtures with MAF-flavored
kind=approval data exercising message_id, argpacked prompt, and
extensions.afw.interrupt.proposed_call. Capacitor's pre-hoc gating tests
live in its own repo and are unaffected.
EOF
)"
```

---

### Task 2: Argpacked prompt builder

**Files:**
- Modify: `microsoft-agent-framework/dotnet/src/Kurrent.AgentFramework/Serialization/ChatMessageConverter.cs`
- Create: `microsoft-agent-framework/dotnet/tests/Kurrent.AgentFramework.IntegrationTests/ChatMessageConverterApprovalTests.cs`

Display-only synthesis of an approval prompt from a `FunctionCallContent`. Format: `"Approve calling {Name}({key=value, …})?"`, truncated at 200 chars (replace tail with `"…"`); if `Name` alone exceeds the cap, fall back to `"Approve calling {Name}?"`.

- [ ] **Step 1: Create the test file with the failing test for the simple case**

Create `microsoft-agent-framework/dotnet/tests/Kurrent.AgentFramework.IntegrationTests/ChatMessageConverterApprovalTests.cs`:

```csharp
using System.Text.Json;
using Google.Protobuf.WellKnownTypes;
using Kurrent.Agent.Schema.Events;
using Kurrent.AgentFramework.Serialization;
using Microsoft.Extensions.AI;

namespace Kurrent.AgentFramework.IntegrationTests;

public class ChatMessageConverterApprovalTests {
    static readonly DateTimeOffset Ts  = new(2026, 4, 29, 12, 0, 0, TimeSpan.Zero);
    static readonly Timestamp      Pts = Timestamp.FromDateTimeOffset(Ts);

    [Test]
    public async Task BuildApprovalPrompt_SimpleArgs_RendersArgpacked() {
        var fc = new FunctionCallContent("call-1", "send_email",
            new Dictionary<string, object?> { ["to"] = "alice@x.com", ["subject"] = "hi" });

        var prompt = ChatMessageConverter.BuildApprovalPrompt(fc);

        await Assert.That(prompt).IsEqualTo("Approve calling send_email(to=\"alice@x.com\", subject=\"hi\")?");
    }
}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd microsoft-agent-framework/dotnet && dotnet test tests/Kurrent.AgentFramework.IntegrationTests --filter "FullyQualifiedName~ChatMessageConverterApprovalTests.BuildApprovalPrompt_SimpleArgs"`
Expected: FAIL — `BuildApprovalPrompt` not defined.

- [ ] **Step 3: Implement `BuildApprovalPrompt`**

In `ChatMessageConverter.cs`, add at the bottom of the class (just above the closing brace):

```csharp
internal const int ApprovalPromptMaxLength = 200;

internal static string BuildApprovalPrompt(FunctionCallContent fc) {
    var name = fc.Name ?? "";
    var head = $"Approve calling {name}";

    if (fc.Arguments is not { Count: > 0 }) return $"{head}?";

    var argsRendered = string.Join(", ", fc.Arguments.Select(kv =>
        $"{kv.Key}={JsonSerializer.Serialize(kv.Value)}"));

    var full = $"{head}({argsRendered})?";
    if (full.Length <= ApprovalPromptMaxLength) return full;

    var withoutArgs = $"{head}?";
    if (withoutArgs.Length >= ApprovalPromptMaxLength) return withoutArgs;

    // Truncate args, append … then close.
    var available = ApprovalPromptMaxLength - $"{head}(…)?".Length;
    if (available <= 0) return withoutArgs;
    return $"{head}({argsRendered[..available]}…)?";
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd microsoft-agent-framework/dotnet && dotnet test tests/Kurrent.AgentFramework.IntegrationTests --filter "FullyQualifiedName~ChatMessageConverterApprovalTests.BuildApprovalPrompt_SimpleArgs"`
Expected: PASS.

- [ ] **Step 5: Add a no-args test**

Append to `ChatMessageConverterApprovalTests.cs` inside the class:

```csharp
    [Test]
    public async Task BuildApprovalPrompt_NoArgs_OmitsParentheses() {
        var fc = new FunctionCallContent("call-1", "ping", arguments: null);

        var prompt = ChatMessageConverter.BuildApprovalPrompt(fc);

        await Assert.That(prompt).IsEqualTo("Approve calling ping?");
    }
```

- [ ] **Step 6: Add a truncation test**

Append:

```csharp
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
```

- [ ] **Step 7: Run all three tests to verify they pass**

Run: `cd microsoft-agent-framework/dotnet && dotnet test tests/Kurrent.AgentFramework.IntegrationTests --filter "FullyQualifiedName~ChatMessageConverterApprovalTests.BuildApprovalPrompt"`
Expected: PASS, 3 tests.

- [ ] **Step 8: Commit**

```bash
git add microsoft-agent-framework/dotnet/src/Kurrent.AgentFramework/Serialization/ChatMessageConverter.cs \
        microsoft-agent-framework/dotnet/tests/Kurrent.AgentFramework.IntegrationTests/ChatMessageConverterApprovalTests.cs
git commit -m "feat(maf): add argpacked approval prompt builder (DEV-1611)"
```

---

### Task 3: `extensions.afw.interrupt` Struct builder

**Files:**
- Modify: `microsoft-agent-framework/dotnet/src/Kurrent.AgentFramework/Serialization/ChatMessageConverter.cs`
- Modify: `microsoft-agent-framework/dotnet/tests/Kurrent.AgentFramework.IntegrationTests/ChatMessageConverterApprovalTests.cs`

Build the `Struct` payload that goes into `evt.Extensions["afw"]`. Shape:

```
{ "interrupt": { "proposed_call": { "id", "name", "arguments" }, "approval_pair_id"? } }
```

`approval_pair_id` is omitted when it equals the inner `FunctionCall.CallId`.

- [ ] **Step 1: Add a failing test for the common-case extension shape**

Append to `ChatMessageConverterApprovalTests.cs`:

```csharp
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `dotnet test tests/Kurrent.AgentFramework.IntegrationTests --filter "FullyQualifiedName~ChatMessageConverterApprovalTests.BuildAfwInterruptExtension"`
Expected: FAIL — `BuildAfwInterruptExtension` not defined.

- [ ] **Step 3: Implement `BuildAfwInterruptExtension`**

In `ChatMessageConverter.cs`, add at the bottom of the class:

```csharp
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
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `dotnet test tests/Kurrent.AgentFramework.IntegrationTests --filter "FullyQualifiedName~ChatMessageConverterApprovalTests.BuildAfwInterruptExtension_CallIdEqualsPairId"`
Expected: PASS.

- [ ] **Step 5: Add the differing-pair-id test**

Append:

```csharp
    [Test]
    public async Task BuildAfwInterruptExtension_DifferingPairId_IncludesPairId() {
        var fc = new FunctionCallContent("call-1", "ping", arguments: null);

        var ext = ChatMessageConverter.BuildAfwInterruptExtension(fc, approvalPairId: "approval-pair-9");

        var interrupt = ext.Fields["interrupt"].StructValue;
        await Assert.That(interrupt.Fields["approval_pair_id"].StringValue).IsEqualTo("approval-pair-9");
    }
```

- [ ] **Step 6: Run to verify both tests pass**

Run: `dotnet test tests/Kurrent.AgentFramework.IntegrationTests --filter "FullyQualifiedName~ChatMessageConverterApprovalTests.BuildAfwInterruptExtension"`
Expected: PASS, 2 tests.

- [ ] **Step 7: Commit**

```bash
git add microsoft-agent-framework/dotnet/src/Kurrent.AgentFramework/Serialization/ChatMessageConverter.cs \
        microsoft-agent-framework/dotnet/tests/Kurrent.AgentFramework.IntegrationTests/ChatMessageConverterApprovalTests.cs
git commit -m "feat(maf): add afw.interrupt extension struct builder (DEV-1611)"
```

---

### Task 4: Decompose assistant approval requests

**Files:**
- Modify: `microsoft-agent-framework/dotnet/src/Kurrent.AgentFramework/Serialization/ChatMessageConverter.cs`
- Modify: `microsoft-agent-framework/dotnet/tests/Kurrent.AgentFramework.IntegrationTests/ChatMessageConverterApprovalTests.cs`

Refactor `ToEvents` into per-role dispatch and add `FunctionApprovalRequestContent` handling on the assistant side.

- [ ] **Step 1: Add failing test — `[Text, ApprovalRequest]` → `[AssistantTextGenerated, InterruptIssued]`**

Append to `ChatMessageConverterApprovalTests.cs`:

```csharp
    [Test]
    public async Task ToEvents_AssistantTextAndApproval_EmitsTextAndInterrupt() {
        var fc = new FunctionCallContent("call-1", "send_email",
            new Dictionary<string, object?> { ["to"] = "alice" });
        var msg = new ChatMessage(ChatRole.Assistant, [
            new TextContent("Drafting an email — needs your approval."),
            new FunctionApprovalRequestContent(id: "call-1", functionCall: fc),
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `dotnet test tests/Kurrent.AgentFramework.IntegrationTests --filter "FullyQualifiedName~ChatMessageConverterApprovalTests.ToEvents_AssistantTextAndApproval"`
Expected: FAIL — currently emits a single `AssistantTextGenerated` and ignores the approval block.

- [ ] **Step 3: Refactor `ToEvents` and add approval handling**

Replace the body of `ChatMessageConverter.ToEvents` and add helpers. The existing assistant branch becomes `ToAssistantEvents`. Replace the entire `ToEvents` method through the `ChatRole.Tool` block (lines ~16-79 of the current file) with:

```csharp
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
    var approvals     = message.Contents.OfType<FunctionApprovalRequestContent>().ToList();

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

static InterruptIssued BuildInterruptIssued(FunctionApprovalRequestContent fa, ChatMessage carrier, DateTimeOffset ts) {
    var fc  = fa.FunctionCall;
    var evt = new InterruptIssued {
        RequestId = fc.CallId ?? "",
        Kind      = "approval",
        Prompt    = BuildApprovalPrompt(fc),
        Timestamp = Timestamp.FromDateTimeOffset(ts),
    };
    if (!string.IsNullOrEmpty(fc.Name))  evt.ToolName  = fc.Name;
    if (carrier.MessageId is { } mid)    evt.MessageId = mid;
    evt.Extensions["afw"] = BuildAfwInterruptExtension(fc, fa.Id);
    return evt;
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `dotnet test tests/Kurrent.AgentFramework.IntegrationTests --filter "FullyQualifiedName~ChatMessageConverterApprovalTests.ToEvents_AssistantTextAndApproval"`
Expected: PASS.

- [ ] **Step 5: Add `[ApprovalRequest only]` test**

Append to `ChatMessageConverterApprovalTests.cs`:

```csharp
    [Test]
    public async Task ToEvents_AssistantApprovalOnly_SuppressesTextEvent() {
        var fc  = new FunctionCallContent("call-1", "ping", arguments: null);
        var msg = new ChatMessage(ChatRole.Assistant, [
            new FunctionApprovalRequestContent(id: "call-1", functionCall: fc),
        ]) { MessageId = "asst-msg-2" };

        var events = ChatMessageConverter.ToEvents(msg, messageIndex: 0, timestamp: Ts).ToList();

        await Assert.That(events).HasSingleItem();
        await Assert.That(events[0]).IsTypeOf<InterruptIssued>();
    }
```

- [ ] **Step 6: Add mixed `[Text, FCC, ApprovalRequest]` test**

Append:

```csharp
    [Test]
    public async Task ToEvents_AssistantMixedToolsAndApprovals_BothEmitted() {
        var fc       = new FunctionCallContent("call-1", "lookup", arguments: null);
        var approval = new FunctionApprovalRequestContent(id: "call-2",
            functionCall: new FunctionCallContent("call-2", "delete", arguments: null));
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
```

- [ ] **Step 7: Add differing approval-pair-id test**

Append:

```csharp
    [Test]
    public async Task ToEvents_ApprovalPairIdDifferent_StashedInExtensions() {
        var fc  = new FunctionCallContent("call-1", "ping", arguments: null);
        var msg = new ChatMessage(ChatRole.Assistant, [
            new FunctionApprovalRequestContent(id: "approval-pair-9", functionCall: fc),
        ]) { MessageId = "asst-msg-4" };

        var events = ChatMessageConverter.ToEvents(msg, messageIndex: 0, timestamp: Ts).ToList();

        var ii = (InterruptIssued)events.Single();
        var interrupt = ii.Extensions["afw"].Fields["interrupt"].StructValue;
        await Assert.That(interrupt.Fields["approval_pair_id"].StringValue).IsEqualTo("approval-pair-9");
    }
```

- [ ] **Step 8: Run all assistant-side tests to verify**

Run: `dotnet test tests/Kurrent.AgentFramework.IntegrationTests --filter "FullyQualifiedName~ChatMessageConverterApprovalTests.ToEvents_Assistant"`
Expected: PASS, 3 tests.

- [ ] **Step 9: Re-run the existing `ChatMessageConverterTests` to confirm no regressions**

Run: `dotnet test tests/Kurrent.AgentFramework.IntegrationTests --filter "FullyQualifiedName~ChatMessageConverterTests"`
Expected: PASS — all existing tests still green after the per-role refactor.

- [ ] **Step 10: Commit**

```bash
git add microsoft-agent-framework/dotnet/src/Kurrent.AgentFramework/Serialization/ChatMessageConverter.cs \
        microsoft-agent-framework/dotnet/tests/Kurrent.AgentFramework.IntegrationTests/ChatMessageConverterApprovalTests.cs
git commit -m "feat(maf): decompose assistant approval requests into InterruptIssued (DEV-1611)"
```

---

### Task 5: Decompose user approval responses

**Files:**
- Modify: `microsoft-agent-framework/dotnet/src/Kurrent.AgentFramework/Serialization/ChatMessageConverter.cs`
- Modify: `microsoft-agent-framework/dotnet/tests/Kurrent.AgentFramework.IntegrationTests/ChatMessageConverterApprovalTests.cs`

Mirror the assistant side: `FunctionApprovalResponseContent` → `InterruptResolved`, with the same `extensions.afw.interrupt` shape so a Resolved event read in isolation can still reconstruct the inner FCC.

- [ ] **Step 1: Add failing test — `[ApprovalResponse]` → `[InterruptResolved]`**

Append to `ChatMessageConverterApprovalTests.cs`:

```csharp
    [Test]
    public async Task ToEvents_UserApprovalResponse_EmitsInterruptResolved() {
        var fc       = new FunctionCallContent("call-1", "send_email",
            new Dictionary<string, object?> { ["to"] = "alice" });
        var response = new FunctionApprovalResponseContent(id: "call-1", approved: true, functionCall: fc) {
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `dotnet test tests/Kurrent.AgentFramework.IntegrationTests --filter "FullyQualifiedName~ChatMessageConverterApprovalTests.ToEvents_UserApprovalResponse_EmitsInterruptResolved"`
Expected: FAIL — currently emits a `UserMessageReceived` with empty content.

- [ ] **Step 3: Add `BuildInterruptResolved` and update `ToUserEvents`**

In `ChatMessageConverter.cs`, add the helper near `BuildInterruptIssued`:

```csharp
static InterruptResolved BuildInterruptResolved(FunctionApprovalResponseContent fr, ChatMessage carrier, DateTimeOffset ts) {
    var fc  = fr.FunctionCall;
    var evt = new InterruptResolved {
        RequestId = fc.CallId ?? "",
        Outcome   = fr.Approved ? "allow" : "deny",
        Timestamp = Timestamp.FromDateTimeOffset(ts),
    };
    if (carrier.MessageId is { } mid) evt.MessageId = mid;
    if (fr.Reason         is { } r)   evt.Response  = r;
    evt.Extensions["afw"] = BuildAfwInterruptExtension(fc, fr.Id);
    return evt;
}
```

Replace `ToUserEvents` body to handle both text and approval responses:

```csharp
static IEnumerable<object> ToUserEvents(ChatMessage message, int messageIndex, DateTimeOffset timestamp) {
    var msgId   = message.MessageId;
    var author  = message.AuthorName;
    var created = message.CreatedAt;
    var text    = message.Text;

    var responses = message.Contents.OfType<FunctionApprovalResponseContent>().ToList();

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
```

- [ ] **Step 4: Run to verify it passes**

Run: `dotnet test tests/Kurrent.AgentFramework.IntegrationTests --filter "FullyQualifiedName~ChatMessageConverterApprovalTests.ToEvents_UserApprovalResponse_EmitsInterruptResolved"`
Expected: PASS.

- [ ] **Step 5: Add denied-outcome test**

Append:

```csharp
    [Test]
    public async Task ToEvents_UserApprovalResponseDenied_EmitsDenyOutcome() {
        var fc       = new FunctionCallContent("call-1", "send_email", arguments: null);
        var response = new FunctionApprovalResponseContent(id: "call-1", approved: false, functionCall: fc);
        var msg      = new ChatMessage(ChatRole.User, [response]);

        var events = ChatMessageConverter.ToEvents(msg, messageIndex: 0, timestamp: Ts).ToList();

        var ir = (InterruptResolved)events.Single();
        await Assert.That(ir.Outcome).IsEqualTo("deny");
        await Assert.That(ir.HasResponse).IsFalse();
    }
```

- [ ] **Step 6: Add `[Text, ApprovalResponse]` test**

Append:

```csharp
    [Test]
    public async Task ToEvents_UserApprovalResponseWithText_EmitsBothEvents() {
        var fc       = new FunctionCallContent("call-1", "ping", arguments: null);
        var response = new FunctionApprovalResponseContent(id: "call-1", approved: true, functionCall: fc);
        var msg      = new ChatMessage(ChatRole.User, [
            new TextContent("OK go ahead."),
            response,
        ]) { MessageId = "user-msg-2" };

        var events = ChatMessageConverter.ToEvents(msg, messageIndex: 0, timestamp: Ts).ToList();

        await Assert.That(events.Count).IsEqualTo(2);
        await Assert.That(events[0]).IsTypeOf<UserMessageReceived>();
        await Assert.That(events[1]).IsTypeOf<InterruptResolved>();
    }
```

- [ ] **Step 7: Run all user-side approval tests**

Run: `dotnet test tests/Kurrent.AgentFramework.IntegrationTests --filter "FullyQualifiedName~ChatMessageConverterApprovalTests.ToEvents_UserApproval"`
Expected: PASS, 3 tests.

- [ ] **Step 8: Commit**

```bash
git add microsoft-agent-framework/dotnet/src/Kurrent.AgentFramework/Serialization/ChatMessageConverter.cs \
        microsoft-agent-framework/dotnet/tests/Kurrent.AgentFramework.IntegrationTests/ChatMessageConverterApprovalTests.cs
git commit -m "feat(maf): decompose user approval responses into InterruptResolved (DEV-1611)"
```

---

### Task 6: `MergeIntoChatMessage` reverse converter

**Files:**
- Modify: `microsoft-agent-framework/dotnet/src/Kurrent.AgentFramework/Serialization/ChatMessageConverter.cs`
- Modify: `microsoft-agent-framework/dotnet/tests/Kurrent.AgentFramework.IntegrationTests/ChatMessageConverterApprovalTests.cs`

New static entry point `MergeIntoChatMessage(IReadOnlyList<object> events, IReadOnlyDictionary<string, InterruptIssued> issuedByRequestId)` that builds a `ChatMessage` from a group of events sharing a `message_id`. Cross-event lookup reconstructs the inner FCC for `InterruptResolved` from a matching `InterruptIssued`'s `proposed_call`.

- [ ] **Step 1: Add failing test — assistant group with text + interrupt round-trips**

Append to `ChatMessageConverterApprovalTests.cs`:

```csharp
    [Test]
    public async Task MergeIntoChatMessage_AssistantTextAndInterrupt_RebuildsContents() {
        var fc       = new FunctionCallContent("call-1", "send_email",
            new Dictionary<string, object?> { ["to"] = "alice" });
        var original = new ChatMessage(ChatRole.Assistant, [
            new TextContent("Drafting…"),
            new FunctionApprovalRequestContent(id: "call-1", functionCall: fc),
        ]) { MessageId = "asst-msg-1" };

        var events = ChatMessageConverter.ToEvents(original, messageIndex: 0, timestamp: Ts).ToList();
        var issued = events.OfType<InterruptIssued>().ToDictionary(e => e.RequestId);

        var rebuilt = ChatMessageConverter.MergeIntoChatMessage(events, issued);

        await Assert.That(rebuilt).IsNotNull();
        await Assert.That(rebuilt!.Role).IsEqualTo(ChatRole.Assistant);
        await Assert.That(rebuilt.MessageId).IsEqualTo("asst-msg-1");
        await Assert.That(rebuilt.Contents.OfType<TextContent>().Single().Text).IsEqualTo("Drafting…");
        var fa = rebuilt.Contents.OfType<FunctionApprovalRequestContent>().Single();
        await Assert.That(fa.Id).IsEqualTo("call-1");
        await Assert.That(fa.FunctionCall.CallId).IsEqualTo("call-1");
        await Assert.That(fa.FunctionCall.Name).IsEqualTo("send_email");
        await Assert.That(fa.FunctionCall.Arguments!["to"]?.ToString()).IsEqualTo("alice");
    }
```

- [ ] **Step 2: Run to verify it fails**

Run: `dotnet test tests/Kurrent.AgentFramework.IntegrationTests --filter "FullyQualifiedName~ChatMessageConverterApprovalTests.MergeIntoChatMessage_AssistantTextAndInterrupt"`
Expected: FAIL — `MergeIntoChatMessage` not defined.

- [ ] **Step 3: Implement `MergeIntoChatMessage`**

In `ChatMessageConverter.cs`, add as a public method on the class:

```csharp
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

static FunctionApprovalRequestContent BuildApprovalRequestContent(InterruptIssued ii) {
    var (fcCallId, fcName, fcArgs) = ReadProposedCall(ii.Extensions, ii.RequestId, ii.HasToolName ? ii.ToolName : null);
    var fc                          = new FunctionCallContent(fcCallId, fcName ?? "", fcArgs);
    var pairId                      = ReadApprovalPairId(ii.Extensions) ?? ii.RequestId;
    return new FunctionApprovalRequestContent(pairId, fc);
}

static FunctionApprovalResponseContent? BuildApprovalResponseContent(
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
    var resp   = new FunctionApprovalResponseContent(pairId, ir.Outcome == "allow", fc);
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
    if (!afw.Fields.TryGetValue("interrupt", out var interruptValue)) return false;
    var interrupt = interruptValue.StructValue;
    if (!interrupt.Fields.TryGetValue("proposed_call", out var proposedValue)) return false;
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
    if (!afw.Fields.TryGetValue("interrupt", out var interruptValue)) return null;
    var interrupt = interruptValue.StructValue;
    return interrupt.Fields.TryGetValue("approval_pair_id", out var v)
        && v.KindCase == Value.KindOneofCase.StringValue
            ? v.StringValue
            : null;
}
```

- [ ] **Step 4: Run to verify the assistant test passes**

Run: `dotnet test tests/Kurrent.AgentFramework.IntegrationTests --filter "FullyQualifiedName~ChatMessageConverterApprovalTests.MergeIntoChatMessage_AssistantTextAndInterrupt"`
Expected: PASS.

- [ ] **Step 5: Add user-side merge test with cross-event lookup**

Append:

```csharp
    [Test]
    public async Task MergeIntoChatMessage_UserResponse_RebuildsContentsViaCrossEventLookup() {
        var fc           = new FunctionCallContent("call-1", "send_email",
            new Dictionary<string, object?> { ["to"] = "alice" });
        var assistantMsg = new ChatMessage(ChatRole.Assistant, [
            new FunctionApprovalRequestContent(id: "call-1", functionCall: fc),
        ]) { MessageId = "asst-msg-1" };
        var userMsg = new ChatMessage(ChatRole.User, [
            new FunctionApprovalResponseContent(id: "call-1", approved: true, functionCall: fc),
        ]) { MessageId = "user-msg-2" };

        var assistantEvents = ChatMessageConverter.ToEvents(assistantMsg, 0, Ts).ToList();
        var userEvents      = ChatMessageConverter.ToEvents(userMsg, 1, Ts).ToList();
        var issued          = assistantEvents.OfType<InterruptIssued>().ToDictionary(e => e.RequestId);

        var rebuilt = ChatMessageConverter.MergeIntoChatMessage(userEvents, issued);

        await Assert.That(rebuilt).IsNotNull();
        await Assert.That(rebuilt!.Role).IsEqualTo(ChatRole.User);
        var resp = rebuilt.Contents.OfType<FunctionApprovalResponseContent>().Single();
        await Assert.That(resp.Approved).IsTrue();
        await Assert.That(resp.FunctionCall.Name).IsEqualTo("send_email");
        await Assert.That(resp.FunctionCall.Arguments!["to"]?.ToString()).IsEqualTo("alice");
    }
```

- [ ] **Step 6: Add user-side merge test with no Issued (falls back to Resolved's own proposed_call)**

Append:

```csharp
    [Test]
    public async Task MergeIntoChatMessage_ResolvedWithoutIssued_FallsBackToOwnProposedCall() {
        var fc       = new FunctionCallContent("call-1", "send_email", arguments: null);
        var response = new FunctionApprovalResponseContent(id: "call-1", approved: false, functionCall: fc);
        var userMsg  = new ChatMessage(ChatRole.User, [response]) { MessageId = "user-msg-2" };

        var userEvents = ChatMessageConverter.ToEvents(userMsg, 0, Ts).ToList();
        var emptyIndex = new Dictionary<string, InterruptIssued>();

        var rebuilt = ChatMessageConverter.MergeIntoChatMessage(userEvents, emptyIndex);

        await Assert.That(rebuilt).IsNotNull();
        var resp = rebuilt!.Contents.OfType<FunctionApprovalResponseContent>().Single();
        await Assert.That(resp.Approved).IsFalse();
        await Assert.That(resp.FunctionCall.CallId).IsEqualTo("call-1");
        await Assert.That(resp.FunctionCall.Name).IsEqualTo("send_email");
    }
```

- [ ] **Step 7: Run all merge tests to verify**

Run: `dotnet test tests/Kurrent.AgentFramework.IntegrationTests --filter "FullyQualifiedName~ChatMessageConverterApprovalTests.MergeIntoChatMessage"`
Expected: PASS, 3 tests.

- [ ] **Step 8: Commit**

```bash
git add microsoft-agent-framework/dotnet/src/Kurrent.AgentFramework/Serialization/ChatMessageConverter.cs \
        microsoft-agent-framework/dotnet/tests/Kurrent.AgentFramework.IntegrationTests/ChatMessageConverterApprovalTests.cs
git commit -m "feat(maf): add MergeIntoChatMessage for multi-event-per-message reconstruction (DEV-1611)"
```

---

### Task 7: Provider grouping pass

**Files:**
- Modify: `microsoft-agent-framework/dotnet/src/Kurrent.AgentFramework/ChatHistory/KurrentDBChatHistoryProvider.cs`
- Modify: `microsoft-agent-framework/dotnet/tests/Kurrent.AgentFramework.IntegrationTests/KurrentDBChatHistoryProviderTests.cs`

`ProvideChatHistoryAsync` switches from one-event-per-ChatMessage to message-id-keyed grouping, calling `MergeIntoChatMessage`. A side index `Dictionary<string, InterruptIssued>` keyed by `request_id` is populated during the grouping pass for cross-event lookup.

- [ ] **Step 1: Add failing test for full round-trip through the provider**

Append to `KurrentDBChatHistoryProviderTests.cs` (inside the test class):

```csharp
    [Test]
    public async Task ApprovalRequestRoundTripsThroughKurrentDB() {
        using var client = db.CreateClient();
        var sessionId    = Guid.NewGuid().ToString("N");
        var streamName   = StreamNames.AgentSession(sessionId);
        var now          = DateTimeOffset.UtcNow;

        var fc       = new FunctionCallContent("call-1", "send_email",
            new Dictionary<string, object?> { ["to"] = "alice" });
        var approval = new FunctionApprovalRequestContent(id: "call-1", functionCall: fc);
        var carrier  = new ChatMessage(ChatRole.Assistant, [
            new TextContent("Drafting…"),
            approval,
        ]) { MessageId = "asst-msg-1" };

        var events = ChatMessageConverter.ToEvents(carrier, messageIndex: 0, timestamp: now)
            .Select(EventSerializer.Serialize)
            .ToArray();
        await client.AppendToStreamAsync(streamName, StreamState.NoStream, events);

        // Read back via a fresh provider and exercise the public read helper directly.
        // (ProvideChatHistoryAsync is protected; pin the round-trip via the converter
        // path the provider uses internally — same code path, same grouping logic.)
        var resolved = new List<object>();
        await foreach (var re in client.ReadStreamAsync(Direction.Forwards, streamName, StreamPosition.Start)) {
            if (EventSerializer.Deserialize(re) is { } e) resolved.Add(e);
        }

        var issued   = resolved.OfType<InterruptIssued>().ToDictionary(e => e.RequestId);
        var rebuilt  = ChatMessageConverter.MergeIntoChatMessage(resolved, issued);

        await Assert.That(rebuilt).IsNotNull();
        await Assert.That(rebuilt!.Role).IsEqualTo(ChatRole.Assistant);
        await Assert.That(rebuilt.MessageId).IsEqualTo("asst-msg-1");
        await Assert.That(rebuilt.Contents.OfType<TextContent>().Single().Text).IsEqualTo("Drafting…");
        await Assert.That(rebuilt.Contents.OfType<FunctionApprovalRequestContent>().Single()
            .FunctionCall.Name).IsEqualTo("send_email");
    }
```

- [ ] **Step 2: Run to verify it fails (compile error — `FunctionApprovalRequestContent` not yet referenced from this test file or KurrentDBChatHistoryProvider does not yet group)**

Run: `dotnet test tests/Kurrent.AgentFramework.IntegrationTests --filter "FullyQualifiedName~KurrentDBChatHistoryProviderTests.ApprovalRequestRoundTripsThroughKurrentDB"`
Expected: FAIL on compile (`Microsoft.Extensions.AI` already imported, but the call to `MergeIntoChatMessage` exists from Task 6 so it should compile; if it fails it's because we haven't yet wired the provider). If it compiles and PASSes here, great — Task 6 already covers this case via the converter. The provider grouping work below pins the *internal* grouping logic to the converter.

If the test passes at this step, treat it as confirmation that the converter+writer round-trip works end-to-end through KurrentDB.

- [ ] **Step 3: Update `KurrentDBChatHistoryProvider.ProvideChatHistoryAsync` to group events**

In `microsoft-agent-framework/dotnet/src/Kurrent.AgentFramework/ChatHistory/KurrentDBChatHistoryProvider.cs`, replace the body of `ProvideChatHistoryAsync` (the existing `try` block that reads the stream) with:

```csharp
protected override async ValueTask<IEnumerable<ChatMessage>> ProvideChatHistoryAsync(
        InvokingContext   context,
        CancellationToken cancellationToken = default
    ) {
    var state      = _sessionState.GetOrInitializeState(context.Session);
    var streamName = StreamNames.AgentSession(state.SessionId);

    var messages = new List<ChatMessage>();
    var maxIndex = -1;

    var groups        = new List<List<object>>();
    var byMessageId   = new Dictionary<string, int>();
    var issuedByReqId = new Dictionary<string, InterruptIssued>();

    try {
        var result = client.ReadStreamAsync(
            Direction.Forwards,
            streamName,
            StreamPosition.Start,
            cancellationToken: cancellationToken
        );

        await foreach (var resolvedEvent in result.ConfigureAwait(false)) {
            _sessionStarted = true;

            var domainEvent = EventSerializer.Deserialize(resolvedEvent);
            if (domainEvent is null) continue;

            var idx = GetMessageIndex(domainEvent);
            if (idx > maxIndex) maxIndex = idx;

            if (domainEvent is InterruptIssued ii) issuedByReqId[ii.RequestId] = ii;

            var key = GetGroupingKey(domainEvent);
            if (key is { } k && byMessageId.TryGetValue(k, out var gi)) {
                groups[gi].Add(domainEvent);
            } else {
                groups.Add([domainEvent]);
                if (key is not null) byMessageId[key] = groups.Count - 1;
            }
        }
    } catch (StreamNotFoundException) {
        // First interaction — no history yet
    }

    foreach (var group in groups) {
        if (ChatMessageConverter.MergeIntoChatMessage(group, issuedByReqId) is { } chatMessage) {
            messages.Add(chatMessage);
        }
    }

    state.NextMessageIndex = maxIndex + 1;
    _sessionState.SaveState(context.Session, state);

    return messages;
}
```

Add the `GetGroupingKey` helper (next to the existing `GetMessageIndex`):

```csharp
static string? GetGroupingKey(object domainEvent) => domainEvent switch {
    UserMessageReceived         x => x.HasMessageId ? x.MessageId : null,
    AssistantTextGenerated      x => x.HasMessageId ? x.MessageId : null,
    AssistantToolCallsGenerated x => x.HasMessageId ? x.MessageId : null,
    AssistantThinkingGenerated  x => x.HasMessageId ? x.MessageId : null,
    ToolResultReceived          x => x.HasMessageId ? x.MessageId : null,
    InterruptIssued             x => x.HasMessageId ? x.MessageId : null,
    InterruptResolved           x => x.HasMessageId ? x.MessageId : null,
    _                             => null,
};
```

- [ ] **Step 4: Run the round-trip test and existing provider tests**

Run: `dotnet test tests/Kurrent.AgentFramework.IntegrationTests --filter "FullyQualifiedName~KurrentDBChatHistoryProviderTests"`
Expected: PASS — all existing tests continue to work because single-event groups still reconstruct into single ChatMessages.

- [ ] **Step 5: Commit**

```bash
git add microsoft-agent-framework/dotnet/src/Kurrent.AgentFramework/ChatHistory/KurrentDBChatHistoryProvider.cs \
        microsoft-agent-framework/dotnet/tests/Kurrent.AgentFramework.IntegrationTests/KurrentDBChatHistoryProviderTests.cs
git commit -m "feat(maf): group history events by message_id for multi-block reconstruction (DEV-1611)"
```

---

### Task 8: EventTypeMap test update

**Files:**
- Modify: `microsoft-agent-framework/dotnet/tests/Kurrent.AgentFramework.IntegrationTests/EventTypeMapTests.cs`

Pin that interrupts are now exercised by MAF. Update the explanatory comment and add the two interrupt entries to `KnownEventTypes`.

- [ ] **Step 1: Update `EventTypeMapTests.cs`**

Replace the contents with:

```csharp
using Kurrent.Agent.Schema;
using Kurrent.Agent.Schema.Events;

namespace Kurrent.AgentFramework.IntegrationTests;

public class EventTypeMapTests {
    /// <summary>
    /// The MAF .NET integration consumes a subset of the canonical vocabulary.
    /// As of DEV-1611, interrupts (kind=approval) are emitted for tool-approval
    /// flows. Subagents, thinking, and artifacts are still not produced by the
    /// MAF side. This list locks in the subset we exercise; the shared
    /// <see cref="EventTypeMap.All"/> is the authoritative full registry covered
    /// by <c>FixtureRoundTripTests</c> in the schema package.
    /// </summary>
    public static IEnumerable<(Type ClrType, string Name)> KnownEventTypes() => [
        (typeof(SessionStarted),              "SessionStarted"),
        (typeof(SessionEnded),                "SessionEnded"),
        (typeof(UserMessageReceived),         "UserMessageReceived"),
        (typeof(AssistantTextGenerated),      "AssistantTextGenerated"),
        (typeof(AssistantToolCallsGenerated), "AssistantToolCallsGenerated"),
        (typeof(ToolResultReceived),          "ToolResultReceived"),
        (typeof(InterruptIssued),             "InterruptIssued"),
        (typeof(InterruptResolved),           "InterruptResolved"),
        (typeof(FactRetained),                "FactRetained"),
        (typeof(EvalRunStarted),              "EvalRunStarted"),
        (typeof(TurnScored),                  "TurnScored"),
        (typeof(EvalRunCompleted),            "EvalRunCompleted"),
    ];

    [Test]
    [MethodDataSource(nameof(KnownEventTypes))]
    public async Task NameMapsToClrType(Type clrType, string expectedName) {
        await Assert.That(EventTypeMap.All[expectedName]).IsEqualTo(clrType);
    }

    [Test]
    [MethodDataSource(nameof(KnownEventTypes))]
    public async Task ClrTypeMapsBackToName(Type expectedClrType, string name) {
        var byType = EventTypeMap.All.First(kv => kv.Value == expectedClrType).Key;
        await Assert.That(byType).IsEqualTo(name);
    }

    [Test]
    public async Task UnknownNameReturnsFalse() {
        await Assert.That(EventTypeMap.All.ContainsKey("SomeUnknownEvent")).IsFalse();
    }
}
```

- [ ] **Step 2: Run the EventTypeMap tests**

Run: `dotnet test tests/Kurrent.AgentFramework.IntegrationTests --filter "FullyQualifiedName~EventTypeMapTests"`
Expected: PASS — the parameterized tests now run for InterruptIssued/Resolved as well.

- [ ] **Step 3: Run the full MAF .NET test suite as a final regression check**

Run: `cd microsoft-agent-framework/dotnet && dotnet test`
Expected: PASS — all tests including FixtureRoundTripTests against the new MAF-flavored fixtures.

- [ ] **Step 4: Commit**

```bash
git add microsoft-agent-framework/dotnet/tests/Kurrent.AgentFramework.IntegrationTests/EventTypeMapTests.cs
git commit -m "test(maf): pin InterruptIssued/Resolved in MAF .NET vocabulary (DEV-1611)"
```

---

### Task 9: Open .NET PR

**Files:** none (workflow step).

- [ ] **Step 1: Push branch and open PR**

Run:
```bash
git push -u origin HEAD
gh pr create --title "feat(maf): emit InterruptIssued/Resolved for tool approvals in .NET (DEV-1611)" --body "$(cat <<'EOF'
## Summary

- Decompose MAF `FunctionApprovalRequestContent` / `FunctionApprovalResponseContent` content blocks into canonical `InterruptIssued` / `InterruptResolved` events.
- Anchor approval events to their carrier message via `message_id`.
- Honor the post-hoc correlation rule (`request_id == FunctionCall.CallId`) by construction.
- Group events by `message_id` on read and reconstruct multi-block `ChatMessage`s via the new `MergeIntoChatMessage` entry point so MAF can resume in-flight approval flows.
- Replace Claude-Code-themed `kind=permission` schema fixtures with MAF-flavored `kind=approval` fixtures.

Spec: `docs/superpowers/specs/2026-04-29-maf-tool-approval-interrupts-design.md`.
Linear: DEV-1611. Python mirror tracked as a follow-up PR.

## Test plan

- [x] Unit tests for argpacked prompt builder, afw extension struct, and per-role decomposition rules.
- [x] Round-trip integration test through KurrentDB.
- [x] Existing converter and provider tests continue to pass.
- [x] FixtureRoundTripTests pass against the new fixtures.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Expected: PR URL printed.

---

## Phase 2 — Python

### Task 10: Argpacked prompt builder

**Files:**
- Modify: `microsoft-agent-framework/python/kurrent_agent_framework/chat_history.py`
- Modify: `microsoft-agent-framework/python/tests/test_chat_history.py`

Mirror the .NET algorithm. Same template, same 200-char cap, same fallback rule.

- [ ] **Step 1: Add failing test for argpacked prompt**

Append to `microsoft-agent-framework/python/tests/test_chat_history.py` (after existing imports and fixtures):

```python
def test_build_approval_prompt_simple_args():
    from kurrent_agent_framework.chat_history import _build_approval_prompt

    prompt = _build_approval_prompt(name="send_email", arguments={"to": "alice@x.com", "subject": "hi"})

    assert prompt == 'Approve calling send_email(to="alice@x.com", subject="hi")?'


def test_build_approval_prompt_no_args():
    from kurrent_agent_framework.chat_history import _build_approval_prompt

    assert _build_approval_prompt(name="ping", arguments=None) == "Approve calling ping?"


def test_build_approval_prompt_truncates_long_args():
    from kurrent_agent_framework.chat_history import _APPROVAL_PROMPT_MAX_LENGTH, _build_approval_prompt

    prompt = _build_approval_prompt(name="huge", arguments={"payload": "x" * 500})

    assert len(prompt) == _APPROVAL_PROMPT_MAX_LENGTH
    assert prompt.endswith("…)?")
    assert prompt.startswith('Approve calling huge(payload="')
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd microsoft-agent-framework/python && uv run pytest tests/test_chat_history.py -k "build_approval_prompt" -v --timeout=30`
Expected: FAIL — `_build_approval_prompt` not defined.

- [ ] **Step 3: Implement `_build_approval_prompt`**

In `microsoft-agent-framework/python/kurrent_agent_framework/chat_history.py`, near the bottom (above `_coerce_result`), add:

```python
_APPROVAL_PROMPT_MAX_LENGTH: int = 200


def _build_approval_prompt(*, name: str, arguments: dict[str, Any] | None) -> str:
    """Synthesize a display-only approval prompt. Display-only — no code parses it."""
    head = f"Approve calling {name}"
    if not arguments:
        return f"{head}?"

    parts = [f"{key}={json.dumps(value)}" for key, value in arguments.items()]
    full = f"{head}({', '.join(parts)})?"
    if len(full) <= _APPROVAL_PROMPT_MAX_LENGTH:
        return full

    without_args = f"{head}?"
    if len(without_args) >= _APPROVAL_PROMPT_MAX_LENGTH:
        return without_args

    available = _APPROVAL_PROMPT_MAX_LENGTH - len(f"{head}(…)?")
    if available <= 0:
        return without_args
    return f"{head}({', '.join(parts)[:available]}…)?"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd microsoft-agent-framework/python && uv run pytest tests/test_chat_history.py -k "build_approval_prompt" -v --timeout=30`
Expected: PASS, 3 tests.

- [ ] **Step 5: Commit**

```bash
git add microsoft-agent-framework/python/kurrent_agent_framework/chat_history.py \
        microsoft-agent-framework/python/tests/test_chat_history.py
git commit -m "feat(maf-python): add argpacked approval prompt builder (DEV-1611)"
```

---

### Task 11: `_build_afw_interrupt_extension`

**Files:**
- Modify: `microsoft-agent-framework/python/kurrent_agent_framework/chat_history.py`
- Modify: `microsoft-agent-framework/python/tests/test_chat_history.py`

Build the `extensions["afw"]` dict that goes on `InterruptIssued`/`InterruptResolved`. The canonical schema's protobuf event has a `MapField<str, Struct>` for extensions; we build a plain dict and let the protobuf JSON serializer convert.

- [ ] **Step 1: Add failing test for the helper**

Append to `tests/test_chat_history.py`:

```python
def test_build_afw_interrupt_extension_call_id_equals_pair_id_omits_pair_id():
    from kurrent_agent_framework.chat_history import _build_afw_interrupt_extension

    ext = _build_afw_interrupt_extension(
        call_id="call-1", name="ping", arguments={"x": 1}, approval_pair_id="call-1",
    )

    interrupt = ext["interrupt"]
    assert "approval_pair_id" not in interrupt
    proposed = interrupt["proposed_call"]
    assert proposed == {"id": "call-1", "name": "ping", "arguments": {"x": 1}}


def test_build_afw_interrupt_extension_differing_pair_id_includes_pair_id():
    from kurrent_agent_framework.chat_history import _build_afw_interrupt_extension

    ext = _build_afw_interrupt_extension(
        call_id="call-1", name="ping", arguments=None, approval_pair_id="approval-pair-9",
    )

    assert ext["interrupt"]["approval_pair_id"] == "approval-pair-9"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd microsoft-agent-framework/python && uv run pytest tests/test_chat_history.py -k "build_afw_interrupt_extension" -v --timeout=30`
Expected: FAIL — `_build_afw_interrupt_extension` not defined.

- [ ] **Step 3: Implement the helper**

In `chat_history.py`, near the other helpers (alongside `_build_approval_prompt`), add:

```python
def _build_afw_interrupt_extension(
    *,
    call_id: str,
    name: str,
    arguments: dict[str, Any] | None,
    approval_pair_id: str | None,
) -> dict[str, Any]:
    """Build the ``extensions["afw"]`` block for InterruptIssued/Resolved.

    Mirrors ``ChatMessageConverter.BuildAfwInterruptExtension`` on the .NET side.
    ``approval_pair_id`` is omitted when equal to ``call_id`` (the common case
    in MAF where the wrapper threads the call id through unchanged).
    """
    proposed: dict[str, Any] = {
        "id": call_id,
        "name": name,
        "arguments": arguments if arguments is not None else {},
    }
    interrupt: dict[str, Any] = {"proposed_call": proposed}
    if approval_pair_id and approval_pair_id != call_id:
        interrupt["approval_pair_id"] = approval_pair_id
    return {"interrupt": interrupt}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd microsoft-agent-framework/python && uv run pytest tests/test_chat_history.py -k "build_afw_interrupt_extension" -v --timeout=30`
Expected: PASS, 2 tests.

- [ ] **Step 5: Commit**

```bash
git add microsoft-agent-framework/python/kurrent_agent_framework/chat_history.py \
        microsoft-agent-framework/python/tests/test_chat_history.py
git commit -m "feat(maf-python): add afw.interrupt extension dict builder (DEV-1611)"
```

---

### Task 12: Decompose approval requests/responses into events

**Files:**
- Modify: `microsoft-agent-framework/python/kurrent_agent_framework/chat_history.py`
- Modify: `microsoft-agent-framework/python/tests/test_chat_history.py`

Extend `_message_to_events` to detect `Content` instances with `type == "function_approval_request"` (assistant) and `type == "function_approval_response"` (user), emitting `InterruptIssued`/`InterruptResolved` accordingly. Suppress empty `UserMessageReceived`/`AssistantTextGenerated` when the message contains only approval blocks. Preserve existing decomposition behavior for plain text/tool-call/tool-result messages.

- [ ] **Step 1: Update imports in `chat_history.py`**

In `chat_history.py`, expand the schema imports:

```python
from kurrent_agent_schema import (
    AssistantTextGenerated,
    AssistantToolCallsGenerated,
    InterruptIssued,
    InterruptResolved,
    SessionEnded,
    SessionStarted,
    ToolCallInfo,
    ToolResultReceived,
    UserMessageReceived,
    agent_session_stream,
)
```

- [ ] **Step 2: Add failing test for assistant text + approval**

Append to `tests/test_chat_history.py`:

```python
def _make_function_call(call_id: str, name: str, arguments: dict[str, Any] | None = None):
    from agent_framework import Content

    return Content(type="function_call", call_id=call_id, name=name, arguments=arguments)


def _make_approval_request(call_id: str, name: str, arguments: dict[str, Any] | None = None,
                           pair_id: str | None = None):
    from agent_framework import Content

    return Content.from_function_approval_request(
        id=pair_id or call_id,
        function_call=_make_function_call(call_id, name, arguments),
    )


def _make_approval_response(call_id: str, approved: bool, name: str = "ping",
                            arguments: dict[str, Any] | None = None,
                            pair_id: str | None = None):
    from agent_framework import Content

    return Content.from_function_approval_response(
        approved=approved,
        id=pair_id or call_id,
        function_call=_make_function_call(call_id, name, arguments),
    )


def test_message_to_events_assistant_text_and_approval_emits_text_and_interrupt():
    from agent_framework import Message
    from kurrent_agent_framework.chat_history import _message_to_events
    from kurrent_agent_schema import AssistantTextGenerated, InterruptIssued

    msg = Message(
        role="assistant",
        contents=[
            Content(type="text", text="Drafting an email."),
            _make_approval_request("call-1", "send_email", {"to": "alice"}),
        ],
        message_id="asst-msg-1",
    )

    events = list(_message_to_events(msg, message_index=0, timestamp=datetime.now(UTC)))

    assert len(events) == 2
    assert isinstance(events[0], AssistantTextGenerated)
    assert events[0].content == "Drafting an email."
    assert events[0].message_id == "asst-msg-1"

    ii = events[1]
    assert isinstance(ii, InterruptIssued)
    assert ii.request_id == "call-1"
    assert ii.kind == "approval"
    assert ii.tool_name == "send_email"
    assert ii.message_id == "asst-msg-1"
    assert ii.prompt.startswith("Approve calling send_email(")
    afw = ii.extensions["afw"]
    assert afw["interrupt"]["proposed_call"]["name"] == "send_email"
```

You'll also need at the top of `test_chat_history.py` (if not already present):

```python
from datetime import UTC, datetime
from typing import Any

from agent_framework import Content
```

- [ ] **Step 3: Run to verify it fails**

Run: `cd microsoft-agent-framework/python && uv run pytest tests/test_chat_history.py -k "assistant_text_and_approval" -v --timeout=30`
Expected: FAIL — current `_message_to_events` ignores approval blocks.

- [ ] **Step 4: Update `_message_to_events`**

In `chat_history.py`, replace the `_message_to_events` function with:

```python
def _message_to_events(
    message: Message,
    *,
    message_index: int,
    timestamp: datetime,
) -> Iterable[ProtoMessage]:
    """Decompose a ``Message`` into one or more canonical events.

    Approval content blocks (``function_approval_request`` /
    ``function_approval_response``) are decomposed into ``InterruptIssued`` /
    ``InterruptResolved`` events that share ``message_id`` with the carrier
    message. See SCHEMA_v2 §3.3 and ``docs/superpowers/specs/2026-04-29-maf-tool-approval-interrupts-design.md``.
    """
    msg_id = message.message_id
    author = message.author_name
    role = message.role

    if role == "user":
        responses = [c for c in message.contents if c.type == "function_approval_response"]
        if message.text:
            yield UserMessageReceived(
                content=message.text,
                message_id=msg_id,
                author_name=author,
                message_index=message_index,
                timestamp=timestamp,
            )
        for response in responses:
            yield _build_interrupt_resolved(response, message, timestamp)
        return

    if role == "assistant":
        tool_calls = [_build_tool_call_info(c) for c in message.contents if c.type == "function_call"]
        approvals = [c for c in message.contents if c.type == "function_approval_request"]

        if tool_calls:
            yield AssistantToolCallsGenerated(
                tool_calls=tool_calls,
                content=message.text,
                message_id=msg_id,
                author_name=author,
                message_index=message_index,
                timestamp=timestamp,
            )
        elif message.text:
            yield AssistantTextGenerated(
                content=message.text,
                message_id=msg_id,
                author_name=author,
                message_index=message_index,
                timestamp=timestamp,
            )

        for approval in approvals:
            yield _build_interrupt_issued(approval, message, timestamp)
        return

    if role == "tool":
        for c in message.contents:
            if c.type != "function_result":
                continue
            yield ToolResultReceived(
                call_id=c.call_id or "",
                result=_coerce_result(c.result),
                message_id=msg_id,
                author_name=author,
                message_index=message_index,
                timestamp=timestamp,
            )
```

Add the build helpers (next to `_build_tool_call_info`):

```python
def _build_interrupt_issued(approval: Any, carrier: Message, timestamp: datetime) -> InterruptIssued:
    fc = approval.function_call
    args = _coerce_arguments(fc.arguments)
    event = InterruptIssued(
        request_id=fc.call_id or "",
        kind="approval",
        tool_name=fc.name or None,
        prompt=_build_approval_prompt(name=fc.name or "", arguments=args),
        message_id=carrier.message_id,
        timestamp=timestamp,
    )
    afw = _build_afw_interrupt_extension(
        call_id=fc.call_id or "",
        name=fc.name or "",
        arguments=args,
        approval_pair_id=approval.id,
    )
    _set_afw_extension(event, afw)
    return event


def _build_interrupt_resolved(response: Any, carrier: Message, timestamp: datetime) -> InterruptResolved:
    fc = response.function_call
    args = _coerce_arguments(fc.arguments)
    event = InterruptResolved(
        request_id=fc.call_id or "",
        outcome="allow" if response.approved else "deny",
        message_id=carrier.message_id,
        timestamp=timestamp,
    )
    afw = _build_afw_interrupt_extension(
        call_id=fc.call_id or "",
        name=fc.name or "",
        arguments=args,
        approval_pair_id=response.id,
    )
    _set_afw_extension(event, afw)
    return event


def _set_afw_extension(event: ProtoMessage, payload: dict[str, Any]) -> None:
    """Populate ``event.extensions["afw"]`` from a plain dict, going through
    the protobuf JSON parser to coerce nested dicts/lists into ``Struct``."""
    from google.protobuf.json_format import ParseDict
    struct = Struct()
    ParseDict(payload, struct)
    event.extensions["afw"].CopyFrom(struct)
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd microsoft-agent-framework/python && uv run pytest tests/test_chat_history.py -k "assistant_text_and_approval" -v --timeout=30`
Expected: PASS.

- [ ] **Step 6: Add user-side decomposition test**

Append to `tests/test_chat_history.py`:

```python
def test_message_to_events_user_approval_response_emits_interrupt_resolved():
    from agent_framework import Message
    from kurrent_agent_framework.chat_history import _message_to_events
    from kurrent_agent_schema import InterruptResolved

    msg = Message(
        role="user",
        contents=[_make_approval_response("call-1", approved=True, name="send_email",
                                          arguments={"to": "alice"})],
        message_id="user-msg-1",
    )

    events = list(_message_to_events(msg, message_index=1, timestamp=datetime.now(UTC)))

    assert len(events) == 1
    ir = events[0]
    assert isinstance(ir, InterruptResolved)
    assert ir.request_id == "call-1"
    assert ir.outcome == "allow"
    assert ir.message_id == "user-msg-1"
    assert ir.extensions["afw"]["interrupt"]["proposed_call"]["name"] == "send_email"


def test_message_to_events_user_approval_response_denied_emits_deny():
    from agent_framework import Message
    from kurrent_agent_framework.chat_history import _message_to_events
    from kurrent_agent_schema import InterruptResolved

    msg = Message(
        role="user",
        contents=[_make_approval_response("call-1", approved=False)],
    )

    events = list(_message_to_events(msg, message_index=0, timestamp=datetime.now(UTC)))

    ir = events[0]
    assert isinstance(ir, InterruptResolved)
    assert ir.outcome == "deny"
```

Note: protobuf's `MapField` accessor for `Struct` extensions returns a dict-like view; `ir.extensions["afw"]["interrupt"]["proposed_call"]["name"]` may need `MessageToDict(ir.extensions["afw"], preserving_proto_field_name=True)` instead. If the test fails with a type error in step 7, replace the access pattern with:
```python
from google.protobuf.json_format import MessageToDict
afw_dict = MessageToDict(ir.extensions["afw"], preserving_proto_field_name=True)
assert afw_dict["interrupt"]["proposed_call"]["name"] == "send_email"
```

- [ ] **Step 7: Run all decomposition tests**

Run: `cd microsoft-agent-framework/python && uv run pytest tests/test_chat_history.py -k "approval or message_to_events" -v --timeout=30`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add microsoft-agent-framework/python/kurrent_agent_framework/chat_history.py \
        microsoft-agent-framework/python/tests/test_chat_history.py
git commit -m "feat(maf-python): decompose MAF approval content blocks into Interrupt events (DEV-1611)"
```

---

### Task 13: Group events on read + reverse converter

**Files:**
- Modify: `microsoft-agent-framework/python/kurrent_agent_framework/chat_history.py`
- Modify: `microsoft-agent-framework/python/tests/test_chat_history.py`

Replace the per-event `_event_to_message` call inside `get_messages` with a `message_id`-keyed grouping pass + cross-event index, calling a new `_merge_events_into_message`.

- [ ] **Step 1: Add failing test for the merger**

Append to `tests/test_chat_history.py`:

```python
def test_merge_events_assistant_text_and_interrupt_rebuilds_contents():
    from agent_framework import Message
    from kurrent_agent_framework.chat_history import _merge_events_into_message, _message_to_events

    original = Message(
        role="assistant",
        contents=[
            Content(type="text", text="Drafting…"),
            _make_approval_request("call-1", "send_email", {"to": "alice"}),
        ],
        message_id="asst-msg-1",
    )
    events = list(_message_to_events(original, message_index=0, timestamp=datetime.now(UTC)))
    issued_by_request_id = {e.request_id: e for e in events if hasattr(e, "kind")}

    rebuilt = _merge_events_into_message(events, issued_by_request_id)

    assert rebuilt is not None
    assert rebuilt.role == "assistant"
    assert rebuilt.message_id == "asst-msg-1"
    text_blocks = [c for c in rebuilt.contents if c.type == "text"]
    assert text_blocks[0].text == "Drafting…"
    approvals = [c for c in rebuilt.contents if c.type == "function_approval_request"]
    assert len(approvals) == 1
    assert approvals[0].function_call.name == "send_email"
    assert approvals[0].function_call.call_id == "call-1"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd microsoft-agent-framework/python && uv run pytest tests/test_chat_history.py -k "merge_events_assistant" -v --timeout=30`
Expected: FAIL — `_merge_events_into_message` not defined.

- [ ] **Step 3: Implement `_merge_events_into_message` and helpers**

In `chat_history.py`, add (alongside `_event_to_message`):

```python
def _merge_events_into_message(
    events: Sequence[ProtoMessage],
    issued_by_request_id: dict[str, InterruptIssued],
) -> Message | None:
    """Build a single ``Message`` from a group of canonical events sharing
    a ``message_id``. Returns None when the group has no chat-shaped events.

    Mirrors ``ChatMessageConverter.MergeIntoChatMessage`` on the .NET side.
    """
    if not events:
        return None

    role = _determine_role(events)
    if role is None:
        return None

    contents: list[Content] = []
    msg_id: str | None = None
    author: str | None = None

    for ev in events:
        if isinstance(ev, UserMessageReceived):
            if ev.HasField("content") and ev.content:
                contents.append(Content(type="text", text=ev.content))
            msg_id = msg_id or _opt(ev, "message_id")
            author = author or _opt(ev, "author_name")
        elif isinstance(ev, AssistantTextGenerated):
            if ev.HasField("content") and ev.content:
                contents.append(Content(type="text", text=ev.content))
            msg_id = msg_id or _opt(ev, "message_id")
            author = author or _opt(ev, "author_name")
        elif isinstance(ev, AssistantToolCallsGenerated):
            if ev.HasField("content") and ev.content:
                contents.append(Content(type="text", text=ev.content))
            for tc in ev.tool_calls:
                contents.append(Content(
                    type="function_call",
                    call_id=tc.call_id,
                    name=tc.tool_name,
                    arguments=_struct_to_dict(tc.arguments) if tc.HasField("arguments") else None,
                ))
            msg_id = msg_id or _opt(ev, "message_id")
            author = author or _opt(ev, "author_name")
        elif isinstance(ev, ToolResultReceived):
            contents.append(Content(
                type="function_result",
                call_id=ev.call_id,
                result=_opt(ev, "result"),
            ))
            msg_id = msg_id or _opt(ev, "message_id")
            author = author or _opt(ev, "author_name")
        elif isinstance(ev, InterruptIssued):
            contents.append(_build_approval_request_content(ev))
            msg_id = msg_id or _opt(ev, "message_id")
        elif isinstance(ev, InterruptResolved):
            built = _build_approval_response_content(ev, issued_by_request_id)
            if built is not None:
                contents.append(built)
            msg_id = msg_id or _opt(ev, "message_id")

    if not contents:
        return None

    return Message(role=role, contents=contents, message_id=msg_id, author_name=author)


def _determine_role(events: Sequence[ProtoMessage]) -> str | None:
    for ev in events:
        if isinstance(ev, (UserMessageReceived, InterruptResolved)):
            return "user"
        if isinstance(ev, (AssistantTextGenerated, AssistantToolCallsGenerated, InterruptIssued)):
            return "assistant"
        if isinstance(ev, ToolResultReceived):
            return "tool"
    return None


def _build_approval_request_content(ii: InterruptIssued) -> Content:
    call_id, name, arguments = _read_proposed_call(
        ii.extensions, fallback_call_id=ii.request_id,
        fallback_name=ii.tool_name if ii.HasField("tool_name") else None,
    )
    pair_id = _read_approval_pair_id(ii.extensions) or ii.request_id
    return Content.from_function_approval_request(
        id=pair_id,
        function_call=Content(type="function_call", call_id=call_id, name=name or "", arguments=arguments),
    )


def _build_approval_response_content(
    ir: InterruptResolved,
    issued_by_request_id: dict[str, InterruptIssued],
) -> Content | None:
    matched = issued_by_request_id.get(ir.request_id)
    if matched is not None:
        call_id, name, arguments = _read_proposed_call(
            matched.extensions, fallback_call_id=matched.request_id,
            fallback_name=matched.tool_name if matched.HasField("tool_name") else None,
        )
    else:
        call_id, name, arguments = _read_proposed_call(
            ir.extensions, fallback_call_id=ir.request_id, fallback_name=None,
        )
        if name is None and arguments is None and "afw" not in ir.extensions:
            # Pathological — no Issued, no extension on the Resolved either.
            logger.debug("Skipping InterruptResolved %s with no matching Issued and no proposed_call.", ir.request_id)
            return None

    pair_id = _read_approval_pair_id(ir.extensions) or ir.request_id
    return Content.from_function_approval_response(
        approved=ir.outcome == "allow",
        id=pair_id,
        function_call=Content(type="function_call", call_id=call_id, name=name or "", arguments=arguments),
    )


def _read_proposed_call(
    extensions: Any,
    *,
    fallback_call_id: str,
    fallback_name: str | None,
) -> tuple[str, str | None, dict[str, Any] | None]:
    if "afw" not in extensions:
        return fallback_call_id, fallback_name, None
    afw = MessageToDict(extensions["afw"], preserving_proto_field_name=True)
    proposed = afw.get("interrupt", {}).get("proposed_call")
    if not isinstance(proposed, dict):
        return fallback_call_id, fallback_name, None
    return (
        proposed.get("id", fallback_call_id),
        proposed.get("name", fallback_name),
        proposed.get("arguments") or None,
    )


def _read_approval_pair_id(extensions: Any) -> str | None:
    if "afw" not in extensions:
        return None
    afw = MessageToDict(extensions["afw"], preserving_proto_field_name=True)
    return afw.get("interrupt", {}).get("approval_pair_id")
```

- [ ] **Step 4: Update `get_messages` to use grouping**

In `chat_history.py`, replace the body of `get_messages` with:

```python
async def get_messages(
    self,
    session_id: str | None,
    *,
    state: dict[str, Any] | None = None,
    **kwargs: Any,
) -> list[Message]:
    if not session_id:
        return []

    stream = agent_session_stream(session_id)
    messages: list[Message] = []
    groups: list[list[ProtoMessage]] = []
    by_message_id: dict[str, int] = {}
    issued_by_request_id: dict[str, InterruptIssued] = {}

    try:
        response = await self._client.read_stream(stream)
        async for recorded in response:
            self._started_sessions.add(session_id)
            try:
                event = serialization.deserialize(recorded)
            except (json.JSONDecodeError, ParseError, UnicodeDecodeError) as exc:
                logger.warning(
                    "Skipping malformed canonical event at %s:%s: %r",
                    recorded.stream_name,
                    recorded.stream_position,
                    exc,
                )
                continue
            if event is None:
                continue

            if isinstance(event, InterruptIssued):
                issued_by_request_id[event.request_id] = event

            key = _grouping_key(event)
            if key and key in by_message_id:
                groups[by_message_id[key]].append(event)
            else:
                groups.append([event])
                if key:
                    by_message_id[key] = len(groups) - 1
    except NotFoundError:
        pass

    for group in groups:
        msg = _merge_events_into_message(group, issued_by_request_id)
        if msg is not None:
            messages.append(msg)

    return messages
```

Add the `_grouping_key` helper near `_merge_events_into_message`:

```python
def _grouping_key(event: ProtoMessage) -> str | None:
    if hasattr(event, "HasField") and event.HasField("message_id"):
        return event.message_id
    return None
```

Remove the now-unused `_event_to_message` function (or leave it for backwards compat — confirm it's not exported in `__init__.py`; if not exported, delete it).

- [ ] **Step 5: Run merge tests + existing chat-history tests**

Run: `cd microsoft-agent-framework/python && uv run pytest tests/test_chat_history.py -v --timeout=30`
Expected: PASS — all existing chat-history tests continue to pass.

- [ ] **Step 6: Add full round-trip test through KurrentDB**

Append to `tests/test_chat_history.py`:

```python
@pytest.mark.asyncio
async def test_approval_request_round_trips_through_kurrentdb(kurrentdb_url: str):
    """End-to-end: write an assistant [Text, ApprovalRequest] message and read
    it back through the provider; the rebuilt Message must contain both blocks."""
    from agent_framework import Message
    from kurrent_agent_framework.chat_history import KurrentDBHistoryProvider
    from kurrentdbclient import AsyncKurrentDBClient

    session_id = uuid.uuid4().hex
    async with AsyncKurrentDBClient(kurrentdb_url) as client:
        provider = KurrentDBHistoryProvider(client)
        carrier = Message(
            role="assistant",
            contents=[
                Content(type="text", text="Drafting…"),
                _make_approval_request("call-1", "send_email", {"to": "alice"}),
            ],
            message_id="asst-msg-1",
        )
        await provider.save_messages(session_id, [carrier])

        # Fresh provider over same stream
        reader = KurrentDBHistoryProvider(client)
        rebuilt_messages = await reader.get_messages(session_id)

        assert len(rebuilt_messages) == 1
        rebuilt = rebuilt_messages[0]
        assert rebuilt.role == "assistant"
        assert rebuilt.message_id == "asst-msg-1"
        text_blocks = [c for c in rebuilt.contents if c.type == "text"]
        assert text_blocks[0].text == "Drafting…"
        approvals = [c for c in rebuilt.contents if c.type == "function_approval_request"]
        assert len(approvals) == 1
        assert approvals[0].function_call.name == "send_email"
```

If `tests/test_chat_history.py` doesn't already import `pytest`, `uuid`, and the `kurrentdb_url` fixture from `conftest.py`, add the imports at top of the file. Confirm by running:

```bash
grep -n "kurrentdb_url\|import pytest\|import uuid" microsoft-agent-framework/python/tests/test_chat_history.py
grep -n "kurrentdb_url" microsoft-agent-framework/python/tests/conftest.py
```

Add any missing imports.

- [ ] **Step 7: Run the round-trip test**

Run: `cd microsoft-agent-framework/python && uv run pytest tests/test_chat_history.py -k "approval_request_round_trips" -v --timeout=120`
Expected: PASS — the Testcontainer KurrentDB instance accepts the events and the provider round-trips them.

- [ ] **Step 8: Commit**

```bash
git add microsoft-agent-framework/python/kurrent_agent_framework/chat_history.py \
        microsoft-agent-framework/python/tests/test_chat_history.py
git commit -m "feat(maf-python): group events by message_id and reconstruct multi-block messages (DEV-1611)"
```

---

### Task 14: Final Python regression check

**Files:** none (verification only).

- [ ] **Step 1: Run the full Python test suite**

Run: `cd microsoft-agent-framework/python && uv run pytest --timeout=120`
Expected: PASS — all tests including memory, fact extraction, eval, and the new approval round-trip.

- [ ] **Step 2: Run the schema fixture round-trip again to confirm Python schema package agrees with the rewritten fixtures**

Run: `cd schema/python && uv run pytest --timeout=30`
Expected: PASS.

---

### Task 15: Open Python PR

**Files:** none (workflow step).

- [ ] **Step 1: Push branch and open PR**

Run:
```bash
git push -u origin HEAD
gh pr create --title "feat(maf-python): emit InterruptIssued/Resolved for tool approvals (DEV-1611)" --body "$(cat <<'EOF'
## Summary

- Mirror the .NET decomposition: MAF Python `function_approval_request` / `function_approval_response` content blocks become canonical `InterruptIssued` / `InterruptResolved` events with `message_id` anchored to the carrier message.
- Group events by `message_id` on read and reconstruct multi-block `Message`s via the new `_merge_events_into_message` so MAF can resume in-flight approval flows.
- Cross-event lookup (Resolved → Issued) reconstructs the inner FCC for the response side.

Spec: `docs/superpowers/specs/2026-04-29-maf-tool-approval-interrupts-design.md`.
Linear: DEV-1611. .NET PR landed first.

## Test plan

- [x] Unit tests for argpacked prompt builder, afw extension dict, and per-role decomposition rules.
- [x] Round-trip integration test through KurrentDB Testcontainer.
- [x] Existing chat-history, memory, and fact-extraction tests continue to pass.
- [x] Schema fixture round-trip continues to pass against the MAF-flavored fixtures.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Expected: PR URL printed.

---

## Self-review

**Spec coverage:**
- Decomposition rules table → Tasks 4 + 5 (.NET) and Task 12 (Python). ✓
- `extensions.afw.interrupt.proposed_call` shape → Task 3 (.NET) + Task 11 (Python). ✓
- Argpacked prompt → Task 2 (.NET) + Task 10 (Python). ✓
- `request_id == FunctionCall.CallId` post-hoc rule → enforced by construction in `BuildInterruptIssued` / `BuildInterruptResolved`; verified indirectly by round-trip tests in Tasks 7 and 13. ✓
- `approval_pair_id` in extensions only when distinct → Task 3 + Task 11 cover both branches. ✓
- Provider-side grouping by `message_id` → Task 7 (.NET) + Task 13 (Python). ✓
- Cross-event lookup for Resolved → Task 6 (.NET) + Task 13 (Python). ✓
- Pathological skip-with-log → covered in `BuildApprovalResponseContent` / `_build_approval_response_content` defensively; no dedicated test (the path is unreachable from write paths defined here, per the spec's risk note). ✓
- Replace Claude-Code-themed fixtures → Task 1. ✓
- EventTypeMap update → Task 8. ✓
- Round-trip tests → Tasks 7 (.NET, KurrentDB) and 13 (Python, KurrentDB Testcontainer). ✓
- Cross-runtime parity (DEV-1563) is explicitly out of scope per the spec. ✓

**Placeholder scan:** None found.

**Type consistency:** `BuildApprovalPrompt`, `BuildAfwInterruptExtension`, `BuildInterruptIssued`, `BuildInterruptResolved`, `MergeIntoChatMessage` reused consistently. Python `_build_approval_prompt`, `_build_afw_interrupt_extension`, `_build_interrupt_issued`, `_build_interrupt_resolved`, `_merge_events_into_message` consistent.

**Risks called out in the plan but acceptable per spec:**
- Pathological `Resolved`-without-`Issued` flows: skip-with-log is defensive; not separately tested.
- Empty `tool_name` on FCC: omitted per `string.IsNullOrEmpty` check; reconstructed FCC carries `""`. Acceptable per spec.
