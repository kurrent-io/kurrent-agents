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

__all__ = [
    "AgentMemory",
    "AgentMemoryContextProvider",
    "FactExtractionOptions",
    "FactExtractionService",
    "FactExtractor",
    "KurrentDBAgentMemory",
    "KurrentDBHistoryProvider",
    "UsageCapture",
    "run_fact_extraction",
    "serialization",
]
