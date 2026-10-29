"""Test configuration for the NAQAA Market suite.

The suite runs identically on SQLite (default) and PostgreSQL.  Set
``DATABASE_URL`` to run it against PostgreSQL — CI does this with a service
container (see .github/workflows/pg-compat-tests.yml).

``pg_compat`` gives every pytest process its own PostgreSQL schema derived
from the module-level ``DATABASE_PATH`` each test file sets, so the per-file
database semantics the tests rely on are preserved on both backends.  Under
pytest the schema name also includes the process id, keeping parallel runs
isolated; schemas are dropped again when the process exits.
"""

import os


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "pg_mode: tests that only run against PostgreSQL")
    if os.environ.get("DATABASE_URL"):
        import pg_compat
        assert pg_compat.is_pg_mode(), (
            "DATABASE_URL is set but pg_compat fell back to SQLite — "
            "check that psycopg2 is installed.")
