"""Kurrent.AgentFramework for Python.

Event-sourced persistence for the Microsoft Agent Framework, backed by KurrentDB.
Wire-compatible with the C# implementation — same event names, same JSON schema,
same stream naming.
"""

from . import events, serialization, stream_name
from .chat_history import KurrentDBHistoryProvider
from .fact_extraction import (
    FactExtractionOptions,
    FactExtractionService,
    FactExtractor,
    run_fact_extraction,
)
from .memory import AgentMemory, AgentMemoryContextProvider, KurrentDBAgentMemory

__all__ = [
    "AgentMemory",
    "AgentMemoryContextProvider",
    "FactExtractionOptions",
    "FactExtractionService",
    "FactExtractor",
    "KurrentDBAgentMemory",
    "KurrentDBHistoryProvider",
    "events",
    "run_fact_extraction",
    "serialization",
    "stream_name",
]
