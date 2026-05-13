"""Shared helpers for showcase runners. Each lane installs this as a
local editable dependency so all three lanes share the dummy
canonical-write path."""

from .dummy import write_dummy_session

__all__ = ["write_dummy_session"]
