from google.protobuf import struct_pb2 as _struct_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class ToolSpec(_message.Message):
    __slots__ = ("name", "description", "input_schema", "source")
    NAME_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    INPUT_SCHEMA_FIELD_NUMBER: _ClassVar[int]
    SOURCE_FIELD_NUMBER: _ClassVar[int]
    name: str
    description: str
    input_schema: _struct_pb2.Struct
    source: str
    def __init__(self, name: _Optional[str] = ..., description: _Optional[str] = ..., input_schema: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., source: _Optional[str] = ...) -> None: ...

class AgentConfig(_message.Message):
    __slots__ = ("tools", "plugins", "conversation_manager", "model_parameters")
    TOOLS_FIELD_NUMBER: _ClassVar[int]
    PLUGINS_FIELD_NUMBER: _ClassVar[int]
    CONVERSATION_MANAGER_FIELD_NUMBER: _ClassVar[int]
    MODEL_PARAMETERS_FIELD_NUMBER: _ClassVar[int]
    tools: _containers.RepeatedCompositeFieldContainer[ToolSpec]
    plugins: _containers.RepeatedScalarFieldContainer[str]
    conversation_manager: _struct_pb2.Struct
    model_parameters: _struct_pb2.Struct
    def __init__(self, tools: _Optional[_Iterable[_Union[ToolSpec, _Mapping]]] = ..., plugins: _Optional[_Iterable[str]] = ..., conversation_manager: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., model_parameters: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ...) -> None: ...

class ToolCallInfo(_message.Message):
    __slots__ = ("call_id", "tool_name", "arguments", "tool_kind")
    CALL_ID_FIELD_NUMBER: _ClassVar[int]
    TOOL_NAME_FIELD_NUMBER: _ClassVar[int]
    ARGUMENTS_FIELD_NUMBER: _ClassVar[int]
    TOOL_KIND_FIELD_NUMBER: _ClassVar[int]
    call_id: str
    tool_name: str
    arguments: _struct_pb2.Struct
    tool_kind: str
    def __init__(self, call_id: _Optional[str] = ..., tool_name: _Optional[str] = ..., arguments: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., tool_kind: _Optional[str] = ...) -> None: ...
