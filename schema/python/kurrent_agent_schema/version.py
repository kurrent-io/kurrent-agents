"""Schema version constant.

Stamped on event metadata under ``$schema_version``. Writers set this to
``SCHEMA_VERSION``; readers that encounter a higher version should either
reject the event or degrade gracefully via ``extra="ignore"`` semantics.
"""

SCHEMA_VERSION: int = 2
"""Kurrent agent event schema version. See ``schema/SCHEMA_v2.md``."""
