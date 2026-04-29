# MAF tool-approval interrupts (DEV-1611)

**Status:** Design
**Linear:** [DEV-1611](https://linear.app/kurrent/issue/DEV-1611)
**Depends on (shipped):** [DEV-1610](https://linear.app/kurrent/issue/DEV-1610) — `message_id` on interrupt events + post-hoc `request_id ↔ tool_call_id` rule + `extensions.{slug}.interrupt.proposed_call` soft convention.
**Related:** DEV-1613 (Capacitor approval UI), DEV-1612 (Capacitor request_id correlation), DEV-1563 (cross-runtime parity).

## Problem

Microsoft Agent Framework (MAF) supports human-in-the-loop tool approval through `ApprovalRequiredAIFunction` (.NET) and `@tool(approval_mode="always_require")` (Python). The agent emits an approval request as a content block inside an assistant message; the caller responds with an approval-response content block inside a user message. Neither MAF .NET nor MAF Python integration in this repo currently translates these blocks into canonical `InterruptIssued`/`InterruptResolved` events — the EventTypeMap in MAF .NET integration tests literally pins this gap with the comment "no interrupts, subagents, thinking, or artifacts from the MAF side yet."

Without canonical interrupt emission:
- Capacitor (and any other reader) cannot render MAF approvals through the framework-agnostic interrupt UI.
- Round-trip persistence is lossy — an in-flight approval cannot be resumed from KurrentDB.
- The post-hoc correlation rule (`InterruptIssued.request_id == eventual AssistantToolCallsGenerated.tool_calls[].call_id`) goes unenforced for the framework that motivated it.

## Goals

1. Decompose MAF approval content blocks into canonical `InterruptIssued`/`InterruptResolved` events on write.
2. Reconstruct the same content blocks on read so MAF can resume an in-flight approval flow from history.
3. Honor the post-hoc correlation rule by construction — `request_id == FunctionCall.CallId`.
4. Preserve cross-runtime structural parity with MAF Python (same canonical fields, same extension keys).
5. Replace Claude-Code-flavored interrupt fixtures in `schema/fixtures/events/` with MAF-flavored fixtures that exercise the canonical contract.

## Non-goals

- `kind=auth` for OAuth/credential gates — no MAF flow surfaces this.
- Streaming approval mid-tool-execution — MAF approval is pre-call; nothing to do.
- `kind=permission` (Claude Code pre-hoc gating) fixture coverage in this repo — Capacitor owns that path.
- Capacitor UI rendering — tracked separately as DEV-1613.
- Byte-equivalent JSON parity between runtimes — this spec satisfies the structural-parity contract; byte-equivalence is DEV-1563's concern.

## Delivery shape

Single design, two PRs (.NET first, Python second). The decomposition rules and `extensions.afw.interrupt` shape are identical across runtimes; staging delivery keeps each PR reviewable while preventing drift between the two implementations of the same contract.

PR 1 (.NET): converter changes, history-provider grouping, fixture replacement, EventTypeMap update, integration tests.
PR 2 (Python): mirror in `kurrent_agent_framework`, integration tests against KurrentDB Testcontainer, schema-package fixture round-trip already exercised by PR 1's fixture rewrite.

## Canonical event mapping

Both runtimes produce the same canonical shape:

### `InterruptIssued`

| Field | Source |
|---|---|
| `request_id` | `FunctionApprovalRequestContent.FunctionCall.CallId` (.NET) / `request.function_call.call_id` (Python) |
| `kind` | `"approval"` (constant) |
| `tool_name` | `FunctionCall.Name` when non-empty; otherwise omitted |
| `prompt` | Synthesized argpacked string `"Approve calling {Name}({arg=value, ...})?"`, truncated at 200 chars. Display-only — no code parses it. |
| `message_id` | Carrier assistant `ChatMessage.MessageId` / `Message.message_id`, when set |
| `timestamp` | Provider's `now`, matching sibling events emitted from the same message |

### `InterruptResolved`

| Field | Source |
|---|---|
| `request_id` | `FunctionApprovalResponseContent.FunctionCall.CallId` |
| `outcome` | `"allow"` if `Approved == true`, else `"deny"` |
| `response` | `Reason` when set |
| `message_id` | Carrier user `ChatMessage.MessageId` |
| `timestamp` | Provider's `now` |

### `extensions.afw.interrupt`

```json
{
  "proposed_call": {
    "id":        "<FunctionCall.CallId>",
    "name":      "<FunctionCall.Name>",
    "arguments": { ... }
  },
  "approval_pair_id": "<FunctionApprovalRequestContent.Id, only when != CallId>"
}
```

`proposed_call` is set on **both** `InterruptIssued` and `InterruptResolved` to enable independent reconstruction of either side. (Read-side reconstruction prefers the Issued event's `proposed_call` when both are present in the stream; falls back to the Resolved event's copy if only the Resolved is read in isolation. See "Read-side" below.)

`approval_pair_id` is omitted when it equals `request_id` (the common case in MAF). Preserved when distinct, so reconstructed `FunctionApprovalRequestContent`/`FunctionApprovalResponseContent` keep their original pair id and MAF's `ProcessFunctionApprovalResponses` can resume.

### Post-hoc correlation rule

`request_id` is the FunctionCall's CallId on **both** the issued and the resolved sides. When the approved call subsequently executes, the resulting `AssistantToolCallsGenerated.tool_calls[].call_id` is the same value. The post-hoc rule from `SCHEMA_v2.md §3.3` holds by construction without per-framework decoding.

## Decomposition rules

Both write paths apply per-content-block decomposition.

**Assistant role:**

| Content blocks | Emitted events (sharing `message_id`) |
|---|---|
| `[Text]` | `AssistantTextGenerated` |
| `[Text, FCC]` | `AssistantToolCallsGenerated{content=Text, tool_calls=[FCC]}` *(today's behavior)* |
| `[Text, ApprovalRequest]` | `AssistantTextGenerated{content=Text}` + `InterruptIssued` |
| `[ApprovalRequest]` (no text, no FCC) | `InterruptIssued` only — empty `AssistantTextGenerated` is suppressed |
| `[Text, FCC, ApprovalRequest]` | `AssistantToolCallsGenerated{content=Text, tool_calls=[FCC]}` + `InterruptIssued` |
| `[ApprovalRequest1, ApprovalRequest2]` | two `InterruptIssued`, one per |

**User role:**

| Content blocks | Emitted events |
|---|---|
| `[Text]` | `UserMessageReceived` *(today)* |
| `[ApprovalResponse]` | `InterruptResolved` only — empty `UserMessageReceived` is suppressed |
| `[Text, ApprovalResponse]` | `UserMessageReceived` + `InterruptResolved` |
| `[ApprovalResponse1, ApprovalResponse2]` | two `InterruptResolved` |

**Tool role:** unchanged.

Text content rides on the "primary" event for the message: `AssistantToolCallsGenerated` if any `FunctionCallContent` exists, otherwise `AssistantTextGenerated`. Approval blocks are always emitted as separate events and never absorb text.

## .NET implementation

### `ChatMessageConverter`

`ToEvents` splits per-role into helpers:

```csharp
public static IEnumerable<object> ToEvents(ChatMessage message, int messageIndex, DateTimeOffset timestamp) {
    if (message.Role == ChatRole.User)      return ToUserEvents(message, messageIndex, timestamp);
    if (message.Role == ChatRole.Assistant) return ToAssistantEvents(message, messageIndex, timestamp);
    if (message.Role == ChatRole.Tool)      return ToToolEvents(message, messageIndex, timestamp);
    return [];
}
```

`ToAssistantEvents`:
1. Collect text from `TextContent` blocks (joined).
2. `fcs = message.Contents.OfType<FunctionCallContent>().ToList()`.
3. `approvals = message.Contents.OfType<FunctionApprovalRequestContent>().ToList()`.
4. Emit primary event:
   - If `fcs.Any()` → `AssistantToolCallsGenerated{ content=text, tool_calls=fcs.Select(BuildToolCallInfo) }`.
   - Else if non-empty text → `AssistantTextGenerated{ content=text }`.
   - Else → no primary event.
5. For each approval → `BuildInterruptIssued(approval, message, timestamp)`.

`ToUserEvents`:
1. Collect text.
2. `responses = message.Contents.OfType<FunctionApprovalResponseContent>().ToList()`.
3. If non-empty text → `UserMessageReceived`. Else suppress.
4. For each response → `BuildInterruptResolved(response, message, timestamp)`.

`ToToolEvents` is unchanged from today.

### Helpers

```csharp
static InterruptIssued BuildInterruptIssued(FunctionApprovalRequestContent fa, ChatMessage carrier, DateTimeOffset ts) {
    var fc  = fa.FunctionCall;
    var evt = new InterruptIssued {
        RequestId = fc.CallId,
        Kind      = "approval",
        Timestamp = Timestamp.FromDateTimeOffset(ts),
    };
    if (!string.IsNullOrEmpty(fc.Name)) evt.ToolName = fc.Name;
    evt.Prompt    = BuildArgpackedPrompt(fc);
    if (carrier.MessageId is { } mid) evt.MessageId = mid;
    evt.Extensions["afw"] = BuildAfwInterruptExtension(fc, fa.Id);
    return evt;
}

static InterruptResolved BuildInterruptResolved(FunctionApprovalResponseContent fr, ChatMessage carrier, DateTimeOffset ts) {
    var fc  = fr.FunctionCall;
    var evt = new InterruptResolved {
        RequestId = fc.CallId,
        Outcome   = fr.Approved ? "allow" : "deny",
        Timestamp = Timestamp.FromDateTimeOffset(ts),
    };
    if (carrier.MessageId is { } mid) evt.MessageId = mid;
    if (fr.Reason       is { } r)   evt.Response  = r;
    evt.Extensions["afw"] = BuildAfwInterruptExtension(fc, fr.Id);
    return evt;
}
```

`BuildAfwInterruptExtension`: returns a `Struct` containing `interrupt.proposed_call = { id, name, arguments }` and, when `approvalPairId != fc.CallId`, `interrupt.approval_pair_id`. Reuses existing `JsonElementToStruct` for the arguments dict.

`BuildArgpackedPrompt`: serializes each argument via `JsonSerializer.Serialize`, joins as `"name=value"` with `", "` separators, wraps in `"Approve calling {Name}({...})?"`, truncates to 200 chars (replace tail with `"…"` past the cap; if `Name` alone exceeds the cap, fall back to `"Approve calling {Name}?"` without args).

### `KurrentDBChatHistoryProvider` — read-side grouping

`ProvideChatHistoryAsync` adds a grouping pass keyed by `message_id`. Pseudocode:

```csharp
var groups        = new List<List<object>>();
var byMessageId   = new Dictionary<string, int>();
var issuedByReqId = new Dictionary<string, InterruptIssued>();

await foreach (var resolvedEvent in result.ConfigureAwait(false)) {
    _sessionStarted = true;
    var domainEvent = EventSerializer.Deserialize(resolvedEvent);
    if (domainEvent is null) continue;

    var idx = GetMessageIndex(domainEvent);
    if (idx > maxIndex) maxIndex = idx;

    if (domainEvent is InterruptIssued ii) issuedByReqId[ii.RequestId] = ii;

    var key = GetGroupingKey(domainEvent);  // message_id when present, else null
    if (key is { } k && byMessageId.TryGetValue(k, out var gi)) {
        groups[gi].Add(domainEvent);
    } else {
        groups.Add([domainEvent]);
        if (key is not null) byMessageId[key] = groups.Count - 1;
    }
}

foreach (var group in groups) {
    if (ChatMessageConverter.MergeIntoChatMessage(group, issuedByReqId) is { } chatMessage)
        messages.Add(chatMessage);
}
```

`GetGroupingKey` returns `message_id` for events that carry one (`UserMessageReceived`, `Assistant*`, `ToolResultReceived`, `InterruptIssued`, `InterruptResolved`); `null` for events that don't (`SessionStarted`, `FactRetained`, etc.).

### `ChatMessageConverter.MergeIntoChatMessage`

New entrypoint replacing per-event `ToChatMessage` for the multi-event-per-message path. Existing single-event `ToChatMessage` stays for backwards compatibility (degenerate case: a one-element group reduces to today's behavior).

Algorithm:

1. Determine `ChatRole` from event types in the group:
   - `UserMessageReceived` or `InterruptResolved` → `User`
   - `Assistant*` or `InterruptIssued` → `Assistant`
   - `ToolResultReceived` → `Tool`
2. Build `AIContent` list in stream order:
   - `UserMessageReceived` / `AssistantTextGenerated` → `TextContent(content)` if non-empty.
   - `AssistantToolCallsGenerated` → `TextContent(content)` (if any) + one `FunctionCallContent` per `tool_calls` entry.
   - `ToolResultReceived` → `FunctionResultContent(call_id, result)`.
   - `InterruptIssued ii` → `FunctionApprovalRequestContent(approval_pair_id ?? ii.RequestId, ReconstructFunctionCall(ii))`. The inner FCC is rebuilt from `extensions.afw.interrupt.proposed_call`.
   - `InterruptResolved ir` → look up `issuedByReqId[ir.RequestId]`. If present, build FCC from the issued event's `proposed_call`; else fall back to the resolved event's own `proposed_call`. Construct `FunctionApprovalResponseContent(approval_pair_id ?? ir.RequestId, ir.Outcome == "allow", FCC)` with `Reason = ir.Response`.
   - If neither side has `proposed_call` (pathological — a Resolved without an Issued and without its own `proposed_call`): skip the event with a debug log; do not fabricate a stub FCC.
3. Carry `MessageId`/`AuthorName`/`CreatedAt` from the first event in the group that has them.
4. Return `null` when no content blocks were built (e.g. group contained only non-chat events).

### Backwards compatibility

Streams written before this change have no interrupts. Grouping is a no-op: each event becomes a one-element group; `MergeIntoChatMessage` with a single non-interrupt event reproduces today's behavior. Existing `ChatMessageConverterTests` and `KurrentDBChatHistoryProviderTests` continue to pass.

## Python implementation

`kurrent_agent_framework` mirrors the .NET layout:

- The Message↔canonical converter module gains `FunctionApprovalRequestContent`/`FunctionApprovalResponseContent` awareness with the same decomposition rules.
- The history provider's read path adds the same `message_id`-keyed grouping pass, plus the same cross-event `issued_by_request_id` lookup for `InterruptResolved` reconstruction.
- The `extensions.afw.interrupt` shape is built as a plain dict, serialized via the canonical Protobuf `Struct` bridge already used by other extension blocks.

Field-name and type mapping:

| Canonical | MAF Python source |
|---|---|
| `request_id` | `request.function_call.call_id` |
| `kind` | `"approval"` |
| `tool_name` | `request.function_call.name` |
| `prompt` | argpacked synthesis (Python implementation, structurally similar to .NET) |
| `message_id` | carrier `Message.message_id` |
| `extensions.afw.interrupt.proposed_call` | `{ id, name, arguments }` from `request.function_call` |
| `extensions.afw.interrupt.approval_pair_id` | `request.id` if `!= call_id` |
| `outcome` | `"allow"` if approved else `"deny"` |
| `response` | `response.reason` if set |

The argpacked prompt is implemented per-runtime; structural similarity is sufficient since `prompt` is display-only.

## Schema fixtures

Replace `schema/fixtures/events/InterruptIssued.json` and `InterruptResolved.json`:

- New `InterruptIssued.json` exercises `kind=approval` with populated `message_id`, `tool_name`, argpacked `prompt`, and `extensions.afw.interrupt.proposed_call`.
- New `InterruptResolved.json` exercises `outcome=allow`, populated `message_id`, and `extensions.afw.interrupt.proposed_call` (mirroring the issued side for independent reconstruction).
- `request_id` is shared between the two fixtures and matches a representative `FunctionCall.CallId`, demonstrating the post-hoc correlation rule.

The pre-existing Claude-Code-flavored fixture data (`kind=permission`, `extensions.claude_code.permission`) is dropped from this repo. Capacitor's pre-hoc gating tests live in its own repo and are unaffected.

`schema/dotnet/Kurrent.Agent.Schema.Tests` and `schema/python/tests` `FixtureRoundTripTests` automatically pick up the new fixtures.

## Tests

### .NET

`Kurrent.AgentFramework.IntegrationTests/ChatMessageConverterApprovalTests.cs` — write-side decomposition, one test per row of the decomposition matrix:

- `ToEvents_AssistantTextAndApproval_EmitsTextAndInterrupt`
- `ToEvents_AssistantApprovalOnly_SuppressesTextEvent`
- `ToEvents_AssistantMixedToolsAndApprovals_BothEmitted`
- `ToEvents_UserApprovalResponse_EmitsInterruptResolved` (covers `Approved=true` → `allow`, `Approved=false` → `deny`)
- `ToEvents_UserApprovalResponseWithText_EmitsBothEvents`
- `ToEvents_UserApprovalResponseWithReason_PopulatesResponseField`
- `ToEvents_ApprovalPairIdEqualsCallId_OmittedFromExtensions`
- `ToEvents_ApprovalPairIdDifferent_StashedInExtensions`

`KurrentDBChatHistoryProviderTests` — end-to-end round-trip:

- `ApprovalRequestRoundTripsThroughKurrentDB`: store assistant `[Text, ApprovalRequest]`, read back, assert reconstructed `ChatMessage` contains a `TextContent` and a `FunctionApprovalRequestContent` with the original FCC fields.
- `ApprovalResponseRoundTripsThroughKurrentDB`: store the approval+response sequence, read back, assert assistant turn carries the request and user turn carries the response with `Approved`/`Reason` preserved.
- `PostHocCorrelationRule_RequestIdMatchesToolCallId`: when the approved call subsequently surfaces as `AssistantToolCallsGenerated`, assert `InterruptIssued.RequestId == AssistantToolCallsGenerated.ToolCalls[0].CallId`.

`EventTypeMapTests.cs`:
- Update line-9 comment ("no interrupts… yet" → reflect interrupts are now exercised).
- Add `(typeof(InterruptIssued), "InterruptIssued")` and `(typeof(InterruptResolved), "InterruptResolved")` to `KnownEventTypes()`.

### Python

`microsoft-agent-framework/python/tests/`:

- `test_canonical_decomposition.py` — same decomposition matrix as .NET, parameterized.
- `test_history_provider_round_trip.py` — extension covering an approval flow against a real KurrentDB Testcontainer. Pass `--timeout=N` per the project memo on persistent-subscription test hangs.

### Cross-runtime parity

Out of scope for this work; folded into DEV-1563. The contract this design guarantees: same canonical fields, same `extensions.afw.interrupt` keys, same `request_id` derivation, same outcome vocabulary. DEV-1563's eventual cross-runtime test enforces structural identity end-to-end.

## Open questions

None at design time. Two empirical questions resolve naturally during implementation:

1. **Does MAF emit a separate `AssistantToolCallsGenerated` for an approved call?** If yes, the post-hoc correlation test asserts `InterruptIssued.RequestId == AssistantToolCallsGenerated.ToolCalls[0].CallId` directly. If no (MAF goes straight from approval response to `FunctionResultContent`), the test asserts on `ToolResultReceived.CallId` instead. Either way the rule holds because both downstream events use the same FCC `CallId`.
2. **Does an approved call's eventual function-call message ever surface as a `FunctionCallContent` block alongside the original `FunctionApprovalRequestContent`?** Mixed-batch decomposition handles it either way (`[Text, FCC, ApprovalRequest]` row), so this is a tested-by-construction case.

## Risks

- **Round-trip fidelity gap:** if MAF's `ProcessFunctionApprovalResponses` consults `FunctionCall.Name` or `Arguments` on the response (not just `CallId`), our reconstructed FCC must carry them. The cross-event lookup from Resolved → Issued → `proposed_call` covers the normal case; the Resolved-side `proposed_call` mirror covers the isolated-read case. Pathological-state skip-with-log is the only remaining failure mode and only fires when `proposed_call` is missing on both sides — unreachable from the write paths defined here.
- **Empty-name FCC:** if the upstream call carries an empty `Name`, `tool_name` is omitted and the canonical event is still valid; renderers fall back to `proposed_call.name` which is also empty in that pathological case. Acceptable.

## Sequencing

1. PR 1: .NET converter + provider + fixtures + tests + EventTypeMap update.
2. PR 2: Python mirror + tests.
3. DEV-1563 (separate ticket) eventually adds the cross-runtime parity check.
