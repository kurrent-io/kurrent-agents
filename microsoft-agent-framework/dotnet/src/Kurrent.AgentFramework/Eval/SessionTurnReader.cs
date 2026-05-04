using System.Text.Json;
using Google.Protobuf;
using Google.Protobuf.WellKnownTypes;
using Kurrent.Agent.Schema;
using Kurrent.Agent.Schema.Events;
using Kurrent.AgentFramework.Serialization;
using KurrentDB.Client;

namespace Kurrent.AgentFramework.Eval;

/// <summary>
/// Reads an agent session stream and groups events into conversation turns.
/// Each turn: user input → [tool calls] → assistant output.
/// </summary>
public static class SessionTurnReader {
    public static async Task<IReadOnlyList<Turn>> ReadTurnsAsync(KurrentDBClient client, string sessionId, CancellationToken ct = default) {
        var streamName = StreamNames.AgentSession(sessionId);
        var turns      = new List<Turn>();

        string? currentInput  = null;
        string? currentOutput = null;
        var     currentTools  = new List<ToolCall>();
        long?   inputTokens   = null;
        long?   outputTokens  = null;
        var     turnIndex     = 0;

        try {
            var events = client.ReadStreamAsync(Direction.Forwards, streamName, StreamPosition.Start, cancellationToken: ct);

            await foreach (var resolved in events.ConfigureAwait(false)) {
                var domainEvent = EventSerializer.Deserialize(resolved);

                if (domainEvent is null) continue;

                switch (domainEvent) {
                    case UserMessageReceived userMsg:
                        // If we have a pending turn, flush it
                        if (currentInput is not null) {
                            turns.Add(new Turn(turnIndex++, currentInput, currentOutput, currentTools.ToList(), inputTokens, outputTokens));
                            currentOutput = null;
                            currentTools.Clear();
                            inputTokens  = null;
                            outputTokens = null;
                        }

                        currentInput = userMsg.HasContent ? userMsg.Content : null;

                        break;

                    case AssistantToolCallsGenerated toolCalls:
                        currentTools.AddRange(
                            toolCalls.ToolCalls.Select(tc => new ToolCall(
                                    tc.ToolName,
                                    StructToJson(tc.Arguments),
                                    null,
                                    false
                                )
                            )
                        );

                        ReadUsageFromMetadata(resolved, ref inputTokens, ref outputTokens);

                        break;

                    case ToolResultReceived toolResult:
                        // Find the matching tool call and set its result
                        for (var i = currentTools.Count - 1; i >= 0; i--) {
                            if (currentTools[i].Result is null) {
                                currentTools[i] = currentTools[i] with { Result = toolResult.HasResult ? toolResult.Result : null };

                                break;
                            }
                        }

                        break;

                    case AssistantTextGenerated assistantMsg:
                        currentOutput = assistantMsg.HasContent ? assistantMsg.Content : null;
                        ReadUsageFromMetadata(resolved, ref inputTokens, ref outputTokens);

                        break;
                }
            }

            // Flush last turn
            if (currentInput is not null) {
                turns.Add(new Turn(turnIndex, currentInput, currentOutput, currentTools.ToList(), inputTokens, outputTokens));
            }
        } catch (StreamNotFoundException) { }

        return turns;
    }

    static string? StructToJson(Struct? args) =>
        args is null || args.Fields.Count == 0
            ? null
            : JsonFormatter.Default.Format(args);

    static void ReadUsageFromMetadata(ResolvedEvent resolved, ref long? inputTokens, ref long? outputTokens) {
        if (resolved.Event.Metadata.Length == 0) return;

        try {
            var meta = JsonSerializer.Deserialize<JsonElement>(resolved.Event.Metadata.Span);

            if (!meta.TryGetProperty("$usage", out var usage)) return;

            if (usage.TryGetProperty("input_tokens", out var inp) && inp.ValueKind == JsonValueKind.Number)
                inputTokens = (inputTokens ?? 0) + inp.GetInt64();

            if (usage.TryGetProperty("output_tokens", out var outp) && outp.ValueKind == JsonValueKind.Number)
                outputTokens = (outputTokens ?? 0) + outp.GetInt64();
        } catch { }
    }
}
