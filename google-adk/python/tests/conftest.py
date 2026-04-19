"""Shared test fixtures."""

from __future__ import annotations

import os

import pytest


def _kurrentdb_connection_string() -> str | None:
    """Optional connection string for integration tests.

    Integration tests that need a live KurrentDB read this env var. Unit tests
    should not require it.
    """
    return os.environ.get("KURRENTDB_CONNECTION_STRING")


@pytest.fixture(scope="session")
def kurrentdb_connection_string() -> str:
    """Skip the test if no KurrentDB is available."""
    value = _kurrentdb_connection_string()
    if not value:
        pytest.skip(
            "KURRENTDB_CONNECTION_STRING not set; integration test skipped. "
            "Run `docker compose up -d` and export the env var to enable."
        )
    return value
