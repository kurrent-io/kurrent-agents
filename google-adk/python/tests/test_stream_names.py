"""Unit tests for stream-name builders and id normalisation."""

from __future__ import annotations

import pytest

from kurrent_google_adk._schema.stream_names import (
    for_app_state,
    for_artifact,
    for_credentials,
    for_eval_run,
    for_memory,
    for_session,
    for_user_state,
)


class TestSessionStream:
    def test_encodes_session_id(self) -> None:
        assert for_session("abc-123") == "AgentSession-abc-123"

    def test_percent_encodes_unsafe_chars(self) -> None:
        # Space is outside the safe set.
        assert for_session("a b") == "AgentSession-a%20b"

    def test_rejects_empty(self) -> None:
        with pytest.raises(ValueError, match="session_id cannot be empty"):
            for_session("")


class TestAppAndUserStateStreams:
    def test_app_state(self) -> None:
        assert for_app_state("my_app") == "AgentAppState-my_app"

    def test_user_state(self) -> None:
        assert for_user_state("my_app", "user-42") == "AgentUserState-my_app-user-42"

    def test_app_name_must_be_identifier(self) -> None:
        with pytest.raises(ValueError, match="must be a valid Python identifier"):
            for_app_state("my-app")  # hyphen is not valid in an identifier

    def test_app_name_cannot_be_user(self) -> None:
        with pytest.raises(ValueError, match='cannot be "user"'):
            for_app_state("user")


class TestArtifactStream:
    def test_session_scoped(self) -> None:
        assert (
            for_artifact(
                app_name="my_app", user_id="u1", session_id="s1", filename="notes.txt"
            )
            == "AgentArtifact-my_app-u1-s1-notes.txt"
        )

    def test_user_scoped_when_session_id_is_none(self) -> None:
        assert (
            for_artifact(app_name="my_app", user_id="u1", filename="notes.txt")
            == "AgentArtifact-my_app-u1-notes.txt"
        )


class TestOtherStreams:
    def test_memory(self) -> None:
        assert for_memory("my_app", "u1") == "AgentMemory-my_app-u1"

    def test_credentials(self) -> None:
        assert for_credentials("my_app", "u1") == "AgentCredentials-my_app-u1"

    def test_eval_run(self) -> None:
        assert for_eval_run("run-9") == "EvalRun-run-9"
