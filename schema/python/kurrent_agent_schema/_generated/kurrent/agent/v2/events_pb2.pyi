import datetime

from google.protobuf import struct_pb2 as _struct_pb2
from google.protobuf import timestamp_pb2 as _timestamp_pb2
from kurrent.agent.v2 import value_types_pb2 as _value_types_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class SessionStarted(_message.Message):
    __slots__ = ("app_name", "agent_name", "model", "tenant_id", "user_id", "agent_config", "previous_session_id", "timestamp", "extensions")
    class ExtensionsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: _struct_pb2.Struct
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ...) -> None: ...
    APP_NAME_FIELD_NUMBER: _ClassVar[int]
    AGENT_NAME_FIELD_NUMBER: _ClassVar[int]
    MODEL_FIELD_NUMBER: _ClassVar[int]
    TENANT_ID_FIELD_NUMBER: _ClassVar[int]
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    AGENT_CONFIG_FIELD_NUMBER: _ClassVar[int]
    PREVIOUS_SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    TIMESTAMP_FIELD_NUMBER: _ClassVar[int]
    EXTENSIONS_FIELD_NUMBER: _ClassVar[int]
    app_name: str
    agent_name: str
    model: str
    tenant_id: str
    user_id: str
    agent_config: _value_types_pb2.AgentConfig
    previous_session_id: str
    timestamp: _timestamp_pb2.Timestamp
    extensions: _containers.MessageMap[str, _struct_pb2.Struct]
    def __init__(self, app_name: _Optional[str] = ..., agent_name: _Optional[str] = ..., model: _Optional[str] = ..., tenant_id: _Optional[str] = ..., user_id: _Optional[str] = ..., agent_config: _Optional[_Union[_value_types_pb2.AgentConfig, _Mapping]] = ..., previous_session_id: _Optional[str] = ..., timestamp: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., extensions: _Optional[_Mapping[str, _struct_pb2.Struct]] = ...) -> None: ...

class SessionEnded(_message.Message):
    __slots__ = ("reason", "timestamp", "extensions")
    class ExtensionsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: _struct_pb2.Struct
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ...) -> None: ...
    REASON_FIELD_NUMBER: _ClassVar[int]
    TIMESTAMP_FIELD_NUMBER: _ClassVar[int]
    EXTENSIONS_FIELD_NUMBER: _ClassVar[int]
    reason: str
    timestamp: _timestamp_pb2.Timestamp
    extensions: _containers.MessageMap[str, _struct_pb2.Struct]
    def __init__(self, reason: _Optional[str] = ..., timestamp: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., extensions: _Optional[_Mapping[str, _struct_pb2.Struct]] = ...) -> None: ...

class SessionContinuedAs(_message.Message):
    __slots__ = ("next_session_id", "reason", "timestamp", "extensions")
    class ExtensionsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: _struct_pb2.Struct
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ...) -> None: ...
    NEXT_SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    TIMESTAMP_FIELD_NUMBER: _ClassVar[int]
    EXTENSIONS_FIELD_NUMBER: _ClassVar[int]
    next_session_id: str
    reason: str
    timestamp: _timestamp_pb2.Timestamp
    extensions: _containers.MessageMap[str, _struct_pb2.Struct]
    def __init__(self, next_session_id: _Optional[str] = ..., reason: _Optional[str] = ..., timestamp: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., extensions: _Optional[_Mapping[str, _struct_pb2.Struct]] = ...) -> None: ...

class UserMessageReceived(_message.Message):
    __slots__ = ("content", "message_id", "author_name", "created_at", "message_index", "timestamp", "extensions")
    class ExtensionsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: _struct_pb2.Struct
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ...) -> None: ...
    CONTENT_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_ID_FIELD_NUMBER: _ClassVar[int]
    AUTHOR_NAME_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_INDEX_FIELD_NUMBER: _ClassVar[int]
    TIMESTAMP_FIELD_NUMBER: _ClassVar[int]
    EXTENSIONS_FIELD_NUMBER: _ClassVar[int]
    content: str
    message_id: str
    author_name: str
    created_at: _timestamp_pb2.Timestamp
    message_index: int
    timestamp: _timestamp_pb2.Timestamp
    extensions: _containers.MessageMap[str, _struct_pb2.Struct]
    def __init__(self, content: _Optional[str] = ..., message_id: _Optional[str] = ..., author_name: _Optional[str] = ..., created_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., message_index: _Optional[int] = ..., timestamp: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., extensions: _Optional[_Mapping[str, _struct_pb2.Struct]] = ...) -> None: ...

class AssistantTextGenerated(_message.Message):
    __slots__ = ("content", "message_id", "author_name", "created_at", "message_index", "timestamp", "extensions")
    class ExtensionsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: _struct_pb2.Struct
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ...) -> None: ...
    CONTENT_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_ID_FIELD_NUMBER: _ClassVar[int]
    AUTHOR_NAME_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_INDEX_FIELD_NUMBER: _ClassVar[int]
    TIMESTAMP_FIELD_NUMBER: _ClassVar[int]
    EXTENSIONS_FIELD_NUMBER: _ClassVar[int]
    content: str
    message_id: str
    author_name: str
    created_at: _timestamp_pb2.Timestamp
    message_index: int
    timestamp: _timestamp_pb2.Timestamp
    extensions: _containers.MessageMap[str, _struct_pb2.Struct]
    def __init__(self, content: _Optional[str] = ..., message_id: _Optional[str] = ..., author_name: _Optional[str] = ..., created_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., message_index: _Optional[int] = ..., timestamp: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., extensions: _Optional[_Mapping[str, _struct_pb2.Struct]] = ...) -> None: ...

class AssistantToolCallsGenerated(_message.Message):
    __slots__ = ("tool_calls", "content", "message_id", "author_name", "created_at", "message_index", "timestamp", "extensions")
    class ExtensionsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: _struct_pb2.Struct
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ...) -> None: ...
    TOOL_CALLS_FIELD_NUMBER: _ClassVar[int]
    CONTENT_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_ID_FIELD_NUMBER: _ClassVar[int]
    AUTHOR_NAME_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_INDEX_FIELD_NUMBER: _ClassVar[int]
    TIMESTAMP_FIELD_NUMBER: _ClassVar[int]
    EXTENSIONS_FIELD_NUMBER: _ClassVar[int]
    tool_calls: _containers.RepeatedCompositeFieldContainer[_value_types_pb2.ToolCallInfo]
    content: str
    message_id: str
    author_name: str
    created_at: _timestamp_pb2.Timestamp
    message_index: int
    timestamp: _timestamp_pb2.Timestamp
    extensions: _containers.MessageMap[str, _struct_pb2.Struct]
    def __init__(self, tool_calls: _Optional[_Iterable[_Union[_value_types_pb2.ToolCallInfo, _Mapping]]] = ..., content: _Optional[str] = ..., message_id: _Optional[str] = ..., author_name: _Optional[str] = ..., created_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., message_index: _Optional[int] = ..., timestamp: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., extensions: _Optional[_Mapping[str, _struct_pb2.Struct]] = ...) -> None: ...

class AssistantThinkingGenerated(_message.Message):
    __slots__ = ("content", "encrypted", "signature", "message_id", "author_name", "created_at", "message_index", "timestamp", "extensions")
    class ExtensionsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: _struct_pb2.Struct
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ...) -> None: ...
    CONTENT_FIELD_NUMBER: _ClassVar[int]
    ENCRYPTED_FIELD_NUMBER: _ClassVar[int]
    SIGNATURE_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_ID_FIELD_NUMBER: _ClassVar[int]
    AUTHOR_NAME_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_INDEX_FIELD_NUMBER: _ClassVar[int]
    TIMESTAMP_FIELD_NUMBER: _ClassVar[int]
    EXTENSIONS_FIELD_NUMBER: _ClassVar[int]
    content: str
    encrypted: bool
    signature: str
    message_id: str
    author_name: str
    created_at: _timestamp_pb2.Timestamp
    message_index: int
    timestamp: _timestamp_pb2.Timestamp
    extensions: _containers.MessageMap[str, _struct_pb2.Struct]
    def __init__(self, content: _Optional[str] = ..., encrypted: _Optional[bool] = ..., signature: _Optional[str] = ..., message_id: _Optional[str] = ..., author_name: _Optional[str] = ..., created_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., message_index: _Optional[int] = ..., timestamp: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., extensions: _Optional[_Mapping[str, _struct_pb2.Struct]] = ...) -> None: ...

class ToolResultReceived(_message.Message):
    __slots__ = ("call_id", "tool_name", "result", "message_id", "author_name", "created_at", "message_index", "timestamp", "extensions")
    class ExtensionsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: _struct_pb2.Struct
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ...) -> None: ...
    CALL_ID_FIELD_NUMBER: _ClassVar[int]
    TOOL_NAME_FIELD_NUMBER: _ClassVar[int]
    RESULT_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_ID_FIELD_NUMBER: _ClassVar[int]
    AUTHOR_NAME_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_INDEX_FIELD_NUMBER: _ClassVar[int]
    TIMESTAMP_FIELD_NUMBER: _ClassVar[int]
    EXTENSIONS_FIELD_NUMBER: _ClassVar[int]
    call_id: str
    tool_name: str
    result: str
    message_id: str
    author_name: str
    created_at: _timestamp_pb2.Timestamp
    message_index: int
    timestamp: _timestamp_pb2.Timestamp
    extensions: _containers.MessageMap[str, _struct_pb2.Struct]
    def __init__(self, call_id: _Optional[str] = ..., tool_name: _Optional[str] = ..., result: _Optional[str] = ..., message_id: _Optional[str] = ..., author_name: _Optional[str] = ..., created_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., message_index: _Optional[int] = ..., timestamp: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., extensions: _Optional[_Mapping[str, _struct_pb2.Struct]] = ...) -> None: ...

class InterruptIssued(_message.Message):
    __slots__ = ("request_id", "kind", "tool_name", "prompt", "message_id", "timestamp", "extensions")
    class ExtensionsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: _struct_pb2.Struct
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ...) -> None: ...
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    TOOL_NAME_FIELD_NUMBER: _ClassVar[int]
    PROMPT_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_ID_FIELD_NUMBER: _ClassVar[int]
    TIMESTAMP_FIELD_NUMBER: _ClassVar[int]
    EXTENSIONS_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    kind: str
    tool_name: str
    prompt: str
    message_id: str
    timestamp: _timestamp_pb2.Timestamp
    extensions: _containers.MessageMap[str, _struct_pb2.Struct]
    def __init__(self, request_id: _Optional[str] = ..., kind: _Optional[str] = ..., tool_name: _Optional[str] = ..., prompt: _Optional[str] = ..., message_id: _Optional[str] = ..., timestamp: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., extensions: _Optional[_Mapping[str, _struct_pb2.Struct]] = ...) -> None: ...

class InterruptResolved(_message.Message):
    __slots__ = ("request_id", "outcome", "response", "message_id", "timestamp", "extensions")
    class ExtensionsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: _struct_pb2.Struct
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ...) -> None: ...
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    OUTCOME_FIELD_NUMBER: _ClassVar[int]
    RESPONSE_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_ID_FIELD_NUMBER: _ClassVar[int]
    TIMESTAMP_FIELD_NUMBER: _ClassVar[int]
    EXTENSIONS_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    outcome: str
    response: str
    message_id: str
    timestamp: _timestamp_pb2.Timestamp
    extensions: _containers.MessageMap[str, _struct_pb2.Struct]
    def __init__(self, request_id: _Optional[str] = ..., outcome: _Optional[str] = ..., response: _Optional[str] = ..., message_id: _Optional[str] = ..., timestamp: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., extensions: _Optional[_Mapping[str, _struct_pb2.Struct]] = ...) -> None: ...

class SubagentStarted(_message.Message):
    __slots__ = ("agent_id", "agent_type", "prompt", "subsession_stream", "timestamp", "extensions")
    class ExtensionsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: _struct_pb2.Struct
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ...) -> None: ...
    AGENT_ID_FIELD_NUMBER: _ClassVar[int]
    AGENT_TYPE_FIELD_NUMBER: _ClassVar[int]
    PROMPT_FIELD_NUMBER: _ClassVar[int]
    SUBSESSION_STREAM_FIELD_NUMBER: _ClassVar[int]
    TIMESTAMP_FIELD_NUMBER: _ClassVar[int]
    EXTENSIONS_FIELD_NUMBER: _ClassVar[int]
    agent_id: str
    agent_type: str
    prompt: str
    subsession_stream: str
    timestamp: _timestamp_pb2.Timestamp
    extensions: _containers.MessageMap[str, _struct_pb2.Struct]
    def __init__(self, agent_id: _Optional[str] = ..., agent_type: _Optional[str] = ..., prompt: _Optional[str] = ..., subsession_stream: _Optional[str] = ..., timestamp: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., extensions: _Optional[_Mapping[str, _struct_pb2.Struct]] = ...) -> None: ...

class SubagentCompleted(_message.Message):
    __slots__ = ("agent_id", "outcome", "summary", "timestamp", "extensions")
    class ExtensionsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: _struct_pb2.Struct
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ...) -> None: ...
    AGENT_ID_FIELD_NUMBER: _ClassVar[int]
    OUTCOME_FIELD_NUMBER: _ClassVar[int]
    SUMMARY_FIELD_NUMBER: _ClassVar[int]
    TIMESTAMP_FIELD_NUMBER: _ClassVar[int]
    EXTENSIONS_FIELD_NUMBER: _ClassVar[int]
    agent_id: str
    outcome: str
    summary: str
    timestamp: _timestamp_pb2.Timestamp
    extensions: _containers.MessageMap[str, _struct_pb2.Struct]
    def __init__(self, agent_id: _Optional[str] = ..., outcome: _Optional[str] = ..., summary: _Optional[str] = ..., timestamp: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., extensions: _Optional[_Mapping[str, _struct_pb2.Struct]] = ...) -> None: ...

class FactRetained(_message.Message):
    __slots__ = ("fact", "retained_at", "extensions")
    class ExtensionsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: _struct_pb2.Struct
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ...) -> None: ...
    FACT_FIELD_NUMBER: _ClassVar[int]
    RETAINED_AT_FIELD_NUMBER: _ClassVar[int]
    EXTENSIONS_FIELD_NUMBER: _ClassVar[int]
    fact: str
    retained_at: _timestamp_pb2.Timestamp
    extensions: _containers.MessageMap[str, _struct_pb2.Struct]
    def __init__(self, fact: _Optional[str] = ..., retained_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., extensions: _Optional[_Mapping[str, _struct_pb2.Struct]] = ...) -> None: ...

class ArtifactVersionCreated(_message.Message):
    __slots__ = ("version", "mime_type", "inline_bytes", "canonical_uri", "custom_metadata", "created_at", "extensions")
    class ExtensionsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: _struct_pb2.Struct
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ...) -> None: ...
    VERSION_FIELD_NUMBER: _ClassVar[int]
    MIME_TYPE_FIELD_NUMBER: _ClassVar[int]
    INLINE_BYTES_FIELD_NUMBER: _ClassVar[int]
    CANONICAL_URI_FIELD_NUMBER: _ClassVar[int]
    CUSTOM_METADATA_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    EXTENSIONS_FIELD_NUMBER: _ClassVar[int]
    version: int
    mime_type: str
    inline_bytes: bytes
    canonical_uri: str
    custom_metadata: _struct_pb2.Struct
    created_at: _timestamp_pb2.Timestamp
    extensions: _containers.MessageMap[str, _struct_pb2.Struct]
    def __init__(self, version: _Optional[int] = ..., mime_type: _Optional[str] = ..., inline_bytes: _Optional[bytes] = ..., canonical_uri: _Optional[str] = ..., custom_metadata: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., created_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., extensions: _Optional[_Mapping[str, _struct_pb2.Struct]] = ...) -> None: ...

class EvalRunStarted(_message.Message):
    __slots__ = ("session_id", "scorer", "criteria", "timestamp", "extensions")
    class ExtensionsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: _struct_pb2.Struct
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ...) -> None: ...
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    SCORER_FIELD_NUMBER: _ClassVar[int]
    CRITERIA_FIELD_NUMBER: _ClassVar[int]
    TIMESTAMP_FIELD_NUMBER: _ClassVar[int]
    EXTENSIONS_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    scorer: str
    criteria: str
    timestamp: _timestamp_pb2.Timestamp
    extensions: _containers.MessageMap[str, _struct_pb2.Struct]
    def __init__(self, session_id: _Optional[str] = ..., scorer: _Optional[str] = ..., criteria: _Optional[str] = ..., timestamp: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., extensions: _Optional[_Mapping[str, _struct_pb2.Struct]] = ...) -> None: ...

class TurnScored(_message.Message):
    __slots__ = ("session_id", "turn_index", "input", "output", "score", "score_label", "reason", "timestamp", "extensions")
    class ExtensionsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: _struct_pb2.Struct
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ...) -> None: ...
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    TURN_INDEX_FIELD_NUMBER: _ClassVar[int]
    INPUT_FIELD_NUMBER: _ClassVar[int]
    OUTPUT_FIELD_NUMBER: _ClassVar[int]
    SCORE_FIELD_NUMBER: _ClassVar[int]
    SCORE_LABEL_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    TIMESTAMP_FIELD_NUMBER: _ClassVar[int]
    EXTENSIONS_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    turn_index: int
    input: str
    output: str
    score: float
    score_label: str
    reason: str
    timestamp: _timestamp_pb2.Timestamp
    extensions: _containers.MessageMap[str, _struct_pb2.Struct]
    def __init__(self, session_id: _Optional[str] = ..., turn_index: _Optional[int] = ..., input: _Optional[str] = ..., output: _Optional[str] = ..., score: _Optional[float] = ..., score_label: _Optional[str] = ..., reason: _Optional[str] = ..., timestamp: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., extensions: _Optional[_Mapping[str, _struct_pb2.Struct]] = ...) -> None: ...

class SessionScored(_message.Message):
    __slots__ = ("session_id", "score", "score_label", "reason", "timestamp", "extensions")
    class ExtensionsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: _struct_pb2.Struct
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ...) -> None: ...
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    SCORE_FIELD_NUMBER: _ClassVar[int]
    SCORE_LABEL_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    TIMESTAMP_FIELD_NUMBER: _ClassVar[int]
    EXTENSIONS_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    score: float
    score_label: str
    reason: str
    timestamp: _timestamp_pb2.Timestamp
    extensions: _containers.MessageMap[str, _struct_pb2.Struct]
    def __init__(self, session_id: _Optional[str] = ..., score: _Optional[float] = ..., score_label: _Optional[str] = ..., reason: _Optional[str] = ..., timestamp: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., extensions: _Optional[_Mapping[str, _struct_pb2.Struct]] = ...) -> None: ...

class EvalRunCompleted(_message.Message):
    __slots__ = ("session_id", "turns_scored", "average_score", "total_cost", "timestamp", "extensions")
    class ExtensionsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: _struct_pb2.Struct
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ...) -> None: ...
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    TURNS_SCORED_FIELD_NUMBER: _ClassVar[int]
    AVERAGE_SCORE_FIELD_NUMBER: _ClassVar[int]
    TOTAL_COST_FIELD_NUMBER: _ClassVar[int]
    TIMESTAMP_FIELD_NUMBER: _ClassVar[int]
    EXTENSIONS_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    turns_scored: int
    average_score: float
    total_cost: float
    timestamp: _timestamp_pb2.Timestamp
    extensions: _containers.MessageMap[str, _struct_pb2.Struct]
    def __init__(self, session_id: _Optional[str] = ..., turns_scored: _Optional[int] = ..., average_score: _Optional[float] = ..., total_cost: _Optional[float] = ..., timestamp: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., extensions: _Optional[_Mapping[str, _struct_pb2.Struct]] = ...) -> None: ...
