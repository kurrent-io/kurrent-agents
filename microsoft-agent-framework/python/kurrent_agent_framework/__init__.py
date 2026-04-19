"""Kurrent.AgentFramework for Python.

Event-sourced persistence for the Microsoft Agent Framework, backed by KurrentDB.
Wire-compatible with the C# implementation — same event names, same JSON schema,
same stream naming.
"""

from . import events, serialization, stream_name
from .chat_history import KurrentDBHistoryProvider
from .memory import AgentMemory, AgentMemoryContextProvider, KurrentDBAgentMemory

__all__ = [
    "AgentMemory",
    "AgentMemoryContextProvider",
    "KurrentDBAgentMemory",
    "KurrentDBHistoryProvider",
    "events",
    "serialization",
    "stream_name",
]
