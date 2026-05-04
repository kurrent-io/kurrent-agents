"""AG-UI Protocol bridge for KurrentDB-backed agent sessions.

See README.md and ``schema/SCHEMA_v2.md`` for the canonical event vocabulary
this bridge consumes.
"""

from kurrent_ag_ui.translator import TranslateContext, translate

__all__ = ["TranslateContext", "translate"]
