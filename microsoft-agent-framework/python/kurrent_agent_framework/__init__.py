"""Kurrent.AgentFramework for Python.

Event-sourced persistence for the Microsoft Agent Framework, backed by KurrentDB.
Canonical event types and stream-name helpers come from the shared
:mod:`kurrent_agent_schema` package (schema v2), so the wire format stays
byte-compatible with every other Kurrent agent integration — including the
MAF .NET mirror.
"""

from . import serialization
from .capture import UsageCapture
from .chat_history import KurrentDBHistoryProvider
from .fact_extraction import (
    FactExtractionOptions,
    FactExtractionService,
    FactExtractor,
    run_fact_extraction,
)
from .memory import AgentMemory, AgentMemoryContextProvider, KurrentDBAgentMemory
from .workflows import (
    AgentTurnTaken,
    GroupChatCompleted,
    KurrentDBCheckpointStorage,
    KurrentDBGroupChatRecorder,
    group_chat_stream,
    workflow_checkpoint_stream,
)

__all__ = [
    "AgentMemory",
    "AgentMemoryContextProvider",
    "AgentTurnTaken",
    "FactExtractionOptions",
    "FactExtractionService",
    "FactExtractor",
    "GroupChatCompleted",
    "KurrentDBAgentMemory",
    "KurrentDBCheckpointStorage",
    "KurrentDBGroupChatRecorder",
    "KurrentDBHistoryProvider",
    "UsageCapture",
    "group_chat_stream",
    "run_fact_extraction",
    "serialization",
    "workflow_checkpoint_stream",
]
