"""ADK ``Event`` ↔ canonical event decomposition and reconstruction.

See ``DESIGN.md`` §5 and ``SCHEMA.md`` §5.2 for the full mapping rules.

**v1 scope.** This codec covers:

- Conversation events: user text, assistant text, assistant tool calls
  (± accompanying text), tool results.
- ADK-specific meta-events: ``AgentTransferred`` (from
  ``actions.transfer_to_agent``), ``Rewind`` (from
  ``actions.rewind_before_invocation_id``), ``Compaction`` (from
  ``actions.compaction``).
- Full round-trip of: ``author``, ``invocation_id``, ``branch``, ``id``,
  ``timestamp``, ``partial``, ``long_running_tool_ids``, and every
  ``EventActions`` field (preserved in ``extensions.adk.actions.*``).
- Usage metadata extraction for the ``$usage`` event-metadata channel.

**Known v1 limitations** (deferred to a later codec pass):

- ``LlmResponse`` metadata (``grounding_metadata``, ``cache_metadata``,
  ``citation_metadata``, ``input_transcription``, ``output_transcription``,
  ``logprobs_result``, etc.) is **not** preserved. Reconstructed events have
  these fields set to ``None``. This is fine for chat-history use cases; if a
  consumer needs grounding citations on replay, a follow-up change adds
  them to ``extensions.adk.llm_response``.
- State routing (``app:`` / ``user:`` prefix splitting to separate streams)
  is the session service's responsibility, not the codec's. The codec
  preserves the full ``state_delta`` verbatim in
  ``extensions.adk.actions.state_delta`` so round-trip is lossless; the
  service is responsible for writing unprefixed keys as ``StateDelta``
  events on the session stream and routing prefixed keys to
  ``AgentAppState`` / ``AgentUserState`` streams.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from google.adk.events.event import Event as AdkEvent
from google.adk.events.event_actions import EventActions, EventCompaction
from google.genai import types

from ._schema.events import (
    ADK_EXTENSION_KEY,
    AgentTransferred,
    AssistantTextGenerated,
    AssistantToolCallsGenerated,
    Compaction,
    Rewind,
    ToolCallInfo,
    ToolResultReceived,
    UserMessageReceived,
    _EventBase as CanonicalEvent,
)


# ----- Write path ------------------------------------------------------------


def event_to_canonical(event: AdkEvent) -> list[CanonicalEvent]:
    """Decompose one ADK ``Event`` into one or more canonical events.

    Returns events in append order. State-delta routing (splitting ``app:`` /
    ``user:`` prefixed keys to separate streams) is the session service's
    responsibility; this codec preserves the full delta in
    ``extensions.adk.actions.state_delta``.
    """
    timestamp = _to_datetime(event.timestamp)
    extensions = {ADK_EXTENSION_KEY: _build_adk_extensions(event)}
    actions = event.actions or EventActions()
    results: list[CanonicalEvent] = []

    # Meta-events — each mutually exclusive with conversation content on the
    # originating ADK event, so we emit them and return early.
    if actions.rewind_before_invocation_id:
        results.append(
            Rewind(
                rewind_before_invocation_id=actions.rewind_before_invocation_id,
                state_delta=dict(actions.state_delta or {}),
                timestamp=timestamp,
                extensions=extensions,
            )
        )
        return results

    if actions.compaction:
        comp = actions.compaction
        results.append(
            Compaction(
                start_timestamp=_to_datetime(comp.start_timestamp),
                end_timestamp=_to_datetime(comp.end_timestamp),
                compacted_content=comp.compacted_content.model_dump(mode="json"),
                timestamp=timestamp,
                extensions=extensions,
            )
        )
        return results

    # Agent transfer may co-exist with content (the LLM often says "let me hand
    # this off to X" as the text part alongside the transfer action).
    if actions.transfer_to_agent:
        results.append(
            AgentTransferred(
                from_agent=event.author if event.author != "user" else None,
                to_agent=actions.transfer_to_agent,
                timestamp=timestamp,
                extensions=extensions,
            )
        )

    # Conversation content
    text_content, function_calls, function_responses = _classify_parts(event.content)

    # Tool responses ride on user-role events (FunctionResponse protocol) or
    # sometimes on non-user authors (rare). Emit each as its own event.
    for fr in function_responses:
        results.append(
            ToolResultReceived(
                call_id=fr.id or "",
                tool_name=fr.name,
                result=_serialize_response(fr.response),
                message_id=event.id,
                author_name=event.author,
                message_index=0,
                timestamp=timestamp,
                extensions=extensions,
            )
        )

    if event.author == "user":
        if text_content is not None:
            results.append(
                UserMessageReceived(
                    content=text_content,
                    message_id=event.id,
                    author_name=event.author,
                    message_index=0,
                    timestamp=timestamp,
                    extensions=extensions,
                )
            )
    else:
        if function_calls:
            results.append(
                AssistantToolCallsGenerated(
                    tool_calls=[_tool_call_info(fc) for fc in function_calls],
                    content=text_content,
                    message_id=event.id,
                    author_name=event.author,
                    message_index=0,
                    timestamp=timestamp,
                    extensions=extensions,
                )
            )
        elif text_content is not None:
            results.append(
                AssistantTextGenerated(
                    content=text_content,
                    message_id=event.id,
                    author_name=event.author,
                    message_index=0,
                    timestamp=timestamp,
                    extensions=extensions,
                )
            )

    # Actions-only events (no content, no handoff) — state_delta or flags.
    # Emit a stub AssistantTextGenerated with empty content so the action state
    # round-trips. Edge case; ADK rarely emits content-less events.
    if not results and _actions_are_meaningful(actions):
        results.append(
            AssistantTextGenerated(
                content=None,
                message_id=event.id,
                author_name=event.author,
                message_index=0,
                timestamp=timestamp,
                extensions=extensions,
            )
        )

    return results


def extract_usage_metadata(event: AdkEvent) -> dict[str, Any] | None:
    """Build the ``$usage`` KurrentDB event-metadata payload from an ADK event.

    Returns ``None`` when the event has no ``usage_metadata``. Canonical shape
    is ``SCHEMA.md §3.4``.
    """
    usage = event.usage_metadata
    if usage is None:
        return None
    payload: dict[str, Any] = {}
    # google.genai.types.GenerateContentResponseUsageMetadata has snake_case fields
    # on the Python side; copy the ones that map to our canonical TokenUsage.
    for src, dst in (
        ("prompt_token_count", "input_tokens"),
        ("candidates_token_count", "output_tokens"),
        ("total_token_count", "total_tokens"),
        ("cached_content_token_count", "cached_input_tokens"),
        ("thoughts_token_count", "reasoning_tokens"),
    ):
        value = getattr(usage, src, None)
        if value is not None:
            payload[dst] = int(value)
    return payload or None


# ----- Read path -------------------------------------------------------------


def canonical_to_events(events: list[CanonicalEvent]) -> list[AdkEvent]:
    """Reconstruct ADK ``Event`` objects from an ordered stream of canonical events.

    Events that share an ``extensions.adk.id`` (the source ADK event's id) are
    grouped back into one ADK ``Event``. Events without an ``id`` extension are
    treated as standalone (one-to-one).
    """
    groups: list[list[CanonicalEvent]] = []
    last_id: str | None | object = _UNSET
    for event in events:
        source_id = _adk_ext(event).get("id")
        if source_id is None or source_id != last_id:
            groups.append([event])
        else:
            groups[-1].append(event)
        last_id = source_id

    return [_reconstruct_one(g) for g in groups]


# ----- Helpers ---------------------------------------------------------------


_UNSET = object()


def _to_datetime(value: float | datetime) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    return datetime.fromtimestamp(value, tz=UTC)


def _to_float(value: datetime) -> float:
    return value.timestamp()


def _classify_parts(
    content: types.Content | None,
) -> tuple[str | None, list[types.FunctionCall], list[types.FunctionResponse]]:
    if content is None or not content.parts:
        return None, [], []
    text_chunks: list[str] = []
    calls: list[types.FunctionCall] = []
    responses: list[types.FunctionResponse] = []
    for part in content.parts:
        if part.text is not None:
            text_chunks.append(part.text)
        if part.function_call is not None:
            calls.append(part.function_call)
        if part.function_response is not None:
            responses.append(part.function_response)
    text = "".join(text_chunks) if text_chunks else None
    return text, calls, responses


def _tool_call_info(fc: types.FunctionCall) -> ToolCallInfo:
    return ToolCallInfo(
        call_id=fc.id or "",
        tool_name=fc.name or "",
        arguments=dict(fc.args) if fc.args else None,
    )


def _serialize_response(response: dict[str, Any] | None) -> str | None:
    if response is None:
        return None
    return json.dumps(response)


def _deserialize_response(result: str | None) -> dict[str, Any] | None:
    if result is None:
        return None
    try:
        parsed = json.loads(result)
    except (json.JSONDecodeError, TypeError):
        return {"result": result}
    if isinstance(parsed, dict):
        return parsed
    return {"result": parsed}


def _actions_are_meaningful(actions: EventActions) -> bool:
    """True when EventActions carries state the codec must preserve."""
    return bool(
        actions.state_delta
        or actions.artifact_delta
        or actions.skip_summarization
        or actions.escalate
        or actions.requested_auth_configs
        or actions.requested_tool_confirmations
        or actions.end_of_agent
        or actions.agent_state
    )


def _build_adk_extensions(event: AdkEvent) -> dict[str, Any]:
    """Preserve every non-canonical ADK field in the extensions envelope."""
    ext: dict[str, Any] = {}
    if event.id:
        ext["id"] = event.id
    if event.invocation_id:
        ext["invocation_id"] = event.invocation_id
    # Author is preserved so meta-events (Rewind, Compaction, pure-actions
    # events) — which don't carry author_name in their canonical shape — can
    # round-trip the original ADK ``Event.author``.
    if event.author:
        ext["author"] = event.author
    if event.branch is not None:
        ext["branch"] = event.branch
    if event.long_running_tool_ids:
        ext["long_running_tool_ids"] = sorted(event.long_running_tool_ids)
    if event.partial is not None:
        ext["partial"] = event.partial

    actions_ext = _actions_to_extensions(event.actions)
    if actions_ext:
        ext["actions"] = actions_ext

    return ext


def _actions_to_extensions(actions: EventActions | None) -> dict[str, Any]:
    """Preserve EventActions fields that don't surface as their own canonical events."""
    if actions is None:
        return {}
    ext: dict[str, Any] = {}
    if actions.skip_summarization is not None:
        ext["skip_summarization"] = actions.skip_summarization
    if actions.state_delta:
        ext["state_delta"] = dict(actions.state_delta)
    if actions.artifact_delta:
        ext["artifact_delta"] = dict(actions.artifact_delta)
    if actions.escalate is not None:
        ext["escalate"] = actions.escalate
    if actions.requested_auth_configs:
        ext["requested_auth_configs"] = {
            key: value.model_dump(mode="json")
            for key, value in actions.requested_auth_configs.items()
        }
    if actions.requested_tool_confirmations:
        ext["requested_tool_confirmations"] = {
            key: value.model_dump(mode="json")
            for key, value in actions.requested_tool_confirmations.items()
        }
    if actions.end_of_agent is not None:
        ext["end_of_agent"] = actions.end_of_agent
    if actions.agent_state is not None:
        ext["agent_state"] = dict(actions.agent_state)
    return ext


def _adk_ext(event: CanonicalEvent) -> dict[str, Any]:
    if event.extensions is None:
        return {}
    return event.extensions.get(ADK_EXTENSION_KEY, {})


def _reconstruct_one(group: list[CanonicalEvent]) -> AdkEvent:
    """Merge canonical events from one decomposition back into a single ADK Event."""
    primary = group[0]
    adk_ext = _adk_ext(primary)
    actions = _extensions_to_actions(adk_ext.get("actions", {}))

    author: str | None = None
    parts: list[types.Part] = []

    for event in group:
        if isinstance(event, Rewind):
            actions.rewind_before_invocation_id = event.rewind_before_invocation_id
            actions.state_delta = dict(event.state_delta)
        elif isinstance(event, Compaction):
            actions.compaction = EventCompaction(
                start_timestamp=event.start_timestamp.timestamp(),
                end_timestamp=event.end_timestamp.timestamp(),
                compacted_content=types.Content.model_validate(event.compacted_content),
            )
        elif isinstance(event, AgentTransferred):
            actions.transfer_to_agent = event.to_agent
            author = event.from_agent or author
        elif isinstance(event, UserMessageReceived):
            author = event.author_name or "user"
            if event.content is not None:
                parts.append(types.Part(text=event.content))
        elif isinstance(event, AssistantTextGenerated):
            author = event.author_name or author
            if event.content is not None:
                parts.append(types.Part(text=event.content))
        elif isinstance(event, AssistantToolCallsGenerated):
            author = event.author_name or author
            if event.content is not None:
                parts.append(types.Part(text=event.content))
            for tc in event.tool_calls:
                parts.append(
                    types.Part(
                        function_call=types.FunctionCall(
                            id=tc.call_id or None,
                            name=tc.tool_name or None,
                            args=dict(tc.arguments) if tc.arguments else None,
                        )
                    )
                )
        elif isinstance(event, ToolResultReceived):
            author = event.author_name or author
            parts.append(
                types.Part(
                    function_response=types.FunctionResponse(
                        id=event.call_id or None,
                        name=event.tool_name,
                        response=_deserialize_response(event.result),
                    )
                )
            )

    if author is None:
        # Meta-events (Rewind, Compaction, pure AgentTransferred) don't carry
        # author_name. Recover from ``extensions.adk.author``.
        author = adk_ext.get("author") or getattr(primary, "author_name", None) or "user"

    kwargs: dict[str, Any] = {
        "author": author,
        "invocation_id": adk_ext.get("invocation_id", ""),
        "actions": actions,
        "timestamp": _to_float(_to_datetime(primary.timestamp)),
    }

    if parts:
        kwargs["content"] = types.Content(
            parts=parts,
            role="user" if author == "user" else "model",
        )
    if "id" in adk_ext:
        kwargs["id"] = adk_ext["id"]
    if "branch" in adk_ext:
        kwargs["branch"] = adk_ext["branch"]
    if "long_running_tool_ids" in adk_ext:
        kwargs["long_running_tool_ids"] = set(adk_ext["long_running_tool_ids"])
    if "partial" in adk_ext:
        kwargs["partial"] = adk_ext["partial"]

    return AdkEvent(**kwargs)


def _extensions_to_actions(ext: dict[str, Any]) -> EventActions:
    """Rebuild an EventActions from the extensions envelope.

    Fields restored here are the ones preserved by ``_actions_to_extensions``;
    meta-event fields (``transfer_to_agent``, ``rewind_before_invocation_id``,
    ``compaction``) are set by the caller from the canonical event types.
    """
    actions = EventActions()
    if "skip_summarization" in ext:
        actions.skip_summarization = ext["skip_summarization"]
    if "state_delta" in ext:
        actions.state_delta = dict(ext["state_delta"])
    if "artifact_delta" in ext:
        actions.artifact_delta = dict(ext["artifact_delta"])
    if "escalate" in ext:
        actions.escalate = ext["escalate"]
    if "end_of_agent" in ext:
        actions.end_of_agent = ext["end_of_agent"]
    if "agent_state" in ext:
        actions.agent_state = dict(ext["agent_state"])
    # requested_auth_configs and requested_tool_confirmations: v2.
    return actions
