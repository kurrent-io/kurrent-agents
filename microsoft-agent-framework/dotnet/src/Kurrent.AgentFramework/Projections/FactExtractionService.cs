using Grpc.Core;
using Kurrent.AgentFramework.Events;
using Kurrent.AgentFramework.Memory;
using Kurrent.AgentFramework.Serialization;
using KurrentDB.Client;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;

namespace Kurrent.AgentFramework.Projections;

/// <summary>
/// Delegate that extracts zero or more facts from a user message.
/// Return the facts as plain strings (e.g. "User's name is Alexey").
/// </summary>
public delegate IEnumerable<string> FactExtractor(string userMessage);

/// <summary>
/// Background service that consumes <c>AgentSession-</c> events via a server-side-filtered
/// persistent subscription on <c>$all</c> and extracts facts from user messages using a
/// pluggable <see cref="FactExtractor"/>. Extracted facts are retained via <see cref="IAgentMemory"/>.
/// </summary>
/// <remarks>
/// Uses a persistent subscription (not a catch-up subscription) so the server retains the
/// acked position across process restarts — without this, a restart would replay the entire
/// log and produce duplicate facts.
/// </remarks>
public sealed partial class FactExtractionService(
        KurrentDBPersistentSubscriptionsClient client,
        IAgentMemory                           memory,
        FactExtractor                          extractor,
        FactExtractionOptions                  options,
        ILogger<FactExtractionService>         logger
    ) : BackgroundService {
    const string StreamPrefix = "AgentSession-";

    protected override async Task ExecuteAsync(CancellationToken stoppingToken) {
        while (!stoppingToken.IsCancellationRequested) {
            try {
                await EnsureSubscription(stoppingToken).ConfigureAwait(false);

                var subscription = client.SubscribeToAll(
                    options.GroupName,
                    cancellationToken: stoppingToken
                );

                await using var _ = ((IAsyncDisposable)subscription).ConfigureAwait(false);

                await foreach (var message in subscription.Messages.WithCancellation(stoppingToken).ConfigureAwait(false)) {
                    if (message is not PersistentSubscriptionMessage.Event(var resolvedEvent, _)) continue;

                    try {
                        await Process(resolvedEvent, stoppingToken).ConfigureAwait(false);
                        await subscription.Ack(resolvedEvent).ConfigureAwait(false);
                    } catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested) {
                        throw;
                    } catch (Exception ex) {
                        logger.LogWarning(
                            ex, "Fact extraction failed for {Stream}/{EventNumber}, nacking for retry",
                            resolvedEvent.Event.EventStreamId, resolvedEvent.Event.EventNumber
                        );
                        await subscription.Nack(PersistentSubscriptionNakEventAction.Retry, ex.Message, resolvedEvent).ConfigureAwait(false);
                    }
                }

                // Messages completed without throwing — subscription dropped gracefully.
                // Back off to avoid tight-looping on resubscribe.
                if (!stoppingToken.IsCancellationRequested) {
                    logger.LogWarning(
                        "Persistent subscription {Group} ended without error, re-subscribing in 5s",
                        options.GroupName
                    );
                    await Task.Delay(5000, stoppingToken).ConfigureAwait(false);
                }
            } catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested) {
                break;
            } catch (Exception ex) {
                logger.LogWarning(ex, "Fact extraction subscription interrupted, restarting in 5s");
                await Task.Delay(5000, stoppingToken).ConfigureAwait(false);
            }
        }
    }

    async Task EnsureSubscription(CancellationToken ct) {
        try {
            await client.GetInfoToAllAsync(options.GroupName, cancellationToken: ct).ConfigureAwait(false);
            return;
        } catch (PersistentSubscriptionNotFoundException) {
            // fall through to create
        }

        var settings = new PersistentSubscriptionSettings(startFrom: options.StartFrom ?? Position.Start);

        try {
            await client.CreateToAllAsync(
                options.GroupName,
                StreamFilter.Prefix(StreamPrefix),
                settings,
                cancellationToken: ct
            ).ConfigureAwait(false);
            LogCreatedPersistentSubscriptionGroup(options.GroupName, StreamPrefix);
        } catch (RpcException ex) when (ex.StatusCode == StatusCode.AlreadyExists) {
            // Another instance created it concurrently — safe to proceed.
            LogPersistentSubscriptionGroupAlreadyExists(options.GroupName);
        }
        // Other exceptions propagate to the outer retry-with-backoff loop in ExecuteAsync.
    }

    async Task Process(ResolvedEvent resolvedEvent, CancellationToken ct) {
        if (resolvedEvent.Event.EventType != "UserMessageReceived") return;

        var domainEvent = EventSerializer.Deserialize(resolvedEvent);

        if (domainEvent is not UserMessageReceived userMsg) return;
        if (string.IsNullOrWhiteSpace(userMsg.Content)) return;

        foreach (var fact in extractor(userMsg.Content)) {
            if (string.IsNullOrWhiteSpace(fact)) continue;

            LogAutoExtractedFact(fact, resolvedEvent.Event.EventStreamId, resolvedEvent.Event.EventNumber);
            await memory.RetainAsync(fact, ct).ConfigureAwait(false);
        }
    }

    [LoggerMessage(LogLevel.Debug, "Auto-extracted fact: {Fact} from {Stream}:{EventNumber}")]
    partial void LogAutoExtractedFact(string fact, string stream, StreamPosition eventNumber);

    [LoggerMessage(LogLevel.Debug, "Persistent subscription {Group} already exists (created concurrently)")]
    partial void LogPersistentSubscriptionGroupAlreadyExists(string group);

    [LoggerMessage(LogLevel.Information, "Created persistent subscription {Group} on $all with prefix filter {Prefix}")]
    partial void LogCreatedPersistentSubscriptionGroup(string group, string prefix);
}
