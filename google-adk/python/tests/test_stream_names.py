"""Unit tests for stream-name builders and id normalisation."""

from __future__ import annotations

import pytest

from kurrent_google_adk._streams import (
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
        assert for_app_state("myapp") == "AppState-myapp"

    def test_user_state(self) -> None:
        assert for_user_state("myapp", "alice") == "UserState-myapp-alice"

    def test_app_name_must_be_identifier(self) -> None:
        with pytest.raises(ValueError, match="must be a valid Python identifier"):
            for_app_state("my-app")  # hyphen is not valid in an identifier

    def test_app_name_cannot_be_user(self) -> None:
        with pytest.raises(ValueError, match='cannot be "user"'):
            for_app_state("user")

    def test_user_state_url_encodes_user_id(self) -> None:
        assert for_user_state("myapp", "alice/bob") == "UserState-myapp-alice%2Fbob"


class TestArtifactStream:
    def test_session_scoped(self) -> None:
        assert (
            for_artifact(
                app_name="myapp", user_id="alice", session_id="s1", filename="doc.pdf"
            )
            == "AgentArtifact-myapp-alice-s1-doc.pdf"
        )

    def test_user_scoped_when_session_id_is_none(self) -> None:
        assert (
            for_artifact(app_name="myapp", user_id="alice", filename="doc.pdf")
            == "AgentArtifact-myapp-alice-doc.pdf"
        )


class TestOtherStreams:
    def test_memory(self) -> None:
        assert for_memory("myapp", "alice") == "AgentMemory-myapp-alice"

    def test_credentials(self) -> None:
        assert for_credentials("myapp", "alice") == "Credentials-myapp-alice"

    def test_eval_run(self) -> None:
        assert for_eval_run("run-1") == "EvalRun-run-1"


class TestCategoryConstants:
    def test_session_category(self) -> None:
        assert CATEGORY_SESSION == "$ce-AgentSession"

    def test_memory_category(self) -> None:
        assert CATEGORY_MEMORY == "$ce-AgentMemory"

    def test_artifact_category(self) -> None:
        assert CATEGORY_ARTIFACT == "$ce-AgentArtifact"

    def test_eval_run_category(self) -> None:
        assert CATEGORY_EVAL_RUN == "$ce-EvalRun"

    def test_app_name_123abc_rejected(self) -> None:
        with pytest.raises(ValueError, match="must be a valid Python identifier"):
            for_app_state("123abc")
