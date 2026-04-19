"""Stream-name helpers — re-export of ``_schema.stream_names`` for brevity.

Separate module so consumers (services, tests) can depend on a stable
internal path even if ``_schema`` later becomes an external dependency.
"""

from __future__ import annotations

from ._schema.stream_names import (
    CATEGORY_ARTIFACT,
    CATEGORY_EVAL_RUN,
    CATEGORY_MEMORY,
    CATEGORY_SESSION,
    for_app_state,
    for_artifact,
    for_credentials,
    for_eval_run,
    for_memory,
    for_session,
    for_user_state,
)

__all__ = [
    "CATEGORY_ARTIFACT",
    "CATEGORY_EVAL_RUN",
    "CATEGORY_MEMORY",
    "CATEGORY_SESSION",
    "for_app_state",
    "for_artifact",
    "for_credentials",
    "for_eval_run",
    "for_memory",
    "for_session",
    "for_user_state",
]
