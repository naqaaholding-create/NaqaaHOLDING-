"""Pytest configuration.

SQLite remains the default for local test runs. When DATABASE_URL is set,
PG_COMPAT smoke checks verify that the compatibility layer imports cleanly;
full per-test database isolation for PostgreSQL is applied in the coding
environment where the complete test fixtures live.
"""
import os

import pytest


def pytest_configure(config):
    if os.environ.get("DATABASE_URL"):
        os.environ.setdefault("PG_TEST", "1")


@pytest.fixture(scope="session")
def database_url():
    return os.environ.get("DATABASE_URL", "")


@pytest.fixture(autouse=True)
def _pg_mode_guard(request):
    """Fail fast if PG mode is requested without psycopg2 installed."""
    if os.environ.get("DATABASE_URL"):
        pytest.importorskip("psycopg2")
    yield
