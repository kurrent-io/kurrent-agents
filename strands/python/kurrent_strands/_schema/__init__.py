"""Canonical schema — Pydantic models and stream names.

Vendored here until a shared ``kurrent-agent-schema`` package is extracted.
At that point this subpackage becomes a thin re-export of the shared module.
"""

from . import events, stream_names

__all__ = ["events", "stream_names"]
