from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

from sqlalchemy import inspect, text


ROOT = Path(__file__).resolve().parents[1]
BACKEND_PATH = ROOT / "backend"
if str(BACKEND_PATH) not in sys.path:
    sys.path.insert(0, str(BACKEND_PATH))

from app.database import create_database_engine
from app.services.sql_executor import QueryExecutionError, execute_validated_sql
from app.services.sql_guardrails import validate_and_sanitize_sql
from validation_helpers import check



SETUP_INSTRUCTIONS = """
[SKIP] PostgreSQL integration test skipped: QUERYMIND_POSTGRES_TEST_URL is not set.

This test only runs against a real PostgreSQL server; it is intentionally not run by
default because this agent cannot create database credentials on your behalf.

To run it against your local PostgreSQL service:
  1. In your own terminal (not through this assistant), create a dedicated test role
     and database, for example:
       createuser --pwprompt querymind_test
       createdb --owner=querymind_test querymind_test
  2. Add QUERYMIND_POSTGRES_TEST_URL to your local .env, for example:
       QUERYMIND_POSTGRES_TEST_URL=postgresql+psycopg://querymind_test:<password>@localhost:5432/querymind_test
  3. Re-run: uv run python scripts/validate_postgres_integration.py

The test creates one temporary table, runs schema discovery and a validated SELECT
through the real executor, confirms the read-only defense-in-depth blocks a write,
then drops the table.
""".strip()


def main() -> int:
    test_url = os.environ.get("QUERYMIND_POSTGRES_TEST_URL", "").strip()
    if not test_url:
        print(SETUP_INSTRUCTIONS)
        return 0

    engine = create_database_engine(test_url)
    check(engine.dialect.name == "postgresql", "the configured test URL uses the postgresql dialect")

    table_name = f"querymind_integration_{uuid.uuid4().hex[:8]}"
    try:
        with engine.begin() as connection:
            connection.execute(
                text(f"CREATE TABLE {table_name} (id INTEGER PRIMARY KEY, label TEXT NOT NULL)")
            )
            connection.execute(
                text(f"INSERT INTO {table_name} (id, label) VALUES (1, 'alpha'), (2, 'beta')")
            )

        schema_catalog = {
            table_name: [column["name"] for column in inspect(engine).get_columns(table_name)]
        }
        check("label" in schema_catalog[table_name], "schema discovery finds the temporary table's columns")

        sanitized_sql = validate_and_sanitize_sql(
            sql=f"SELECT label FROM {table_name} ORDER BY id",
            dialect="postgresql",
            question="List labels",
            schema_catalog=schema_catalog,
            relevant_tables=[table_name],
            relevant_columns=[(table_name, "label")],
            examples=[],
            max_rows=100,
        )
        result = execute_validated_sql(engine, sanitized_sql, max_rows=100, timeout_seconds=5.0)
        check(result.columns == ["label"], "the executor runs guardrail-validated SQL against PostgreSQL")
        check(result.rows == [["alpha"], ["beta"]], "the executor returns the expected PostgreSQL rows")

        try:
            execute_validated_sql(
                engine,
                f"DELETE FROM {table_name}",
                max_rows=100,
                timeout_seconds=5.0,
            )
        except QueryExecutionError:
            check(True, "the read-only defense-in-depth blocks writes against PostgreSQL")
        else:
            raise AssertionError("A write statement was executed against the read-only PostgreSQL connection")
    finally:
        with engine.begin() as connection:
            connection.execute(text(f"DROP TABLE IF EXISTS {table_name}"))
        engine.dispose()

    print("PostgreSQL integration test passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
