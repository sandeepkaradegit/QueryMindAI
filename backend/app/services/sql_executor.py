from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass

from sqlalchemy.engine import Engine


class QueryExecutionError(RuntimeError):
    """Raised when already-validated SQL cannot be executed against the configured database."""


class QueryExecutionTimeoutError(QueryExecutionError):
    """Raised when already-validated SQL exceeds the configured execution timeout."""


@dataclass(frozen=True)
class QueryExecutionResult:
    columns: list[str]
    rows: list[list[object]]
    row_count: int
    latency_ms: float


# Defense in depth: reject writes at the connection level even though the SQL
# guardrails already restrict generated statements to a single read-only query.
_READ_ONLY_STATEMENTS = {
    "sqlite": "PRAGMA query_only = ON",
    "postgresql": "SET TRANSACTION READ ONLY",
    "mysql": "SET SESSION TRANSACTION READ ONLY",
}


def execute_validated_sql(
    engine: Engine,
    sql: str,
    *,
    max_rows: int,
    timeout_seconds: float,
) -> QueryExecutionResult:
    dialect_name = engine.dialect.name
    start = time.perf_counter()
    connection = engine.connect()
    try:
        read_only_statement = _READ_ONLY_STATEMENTS.get(dialect_name)
        if read_only_statement:
            connection.exec_driver_sql(read_only_statement)

        def _run() -> tuple[list[str], list[list[object]]]:
            result = connection.exec_driver_sql(sql)
            columns = list(result.keys())
            rows = [list(row) for row in result.fetchmany(max_rows)]
            return columns, rows

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_run)
            try:
                columns, rows = future.result(timeout=timeout_seconds)
            except FutureTimeoutError:
                raw_connection = connection.connection
                interrupt = getattr(raw_connection, "interrupt", None)
                if callable(interrupt):
                    interrupt()
                raise QueryExecutionTimeoutError(
                    f"The query did not complete within {timeout_seconds:.1f} seconds."
                ) from None
    except QueryExecutionTimeoutError:
        raise
    except Exception as error:
        raise QueryExecutionError(
            f"The validated query could not be executed. Database error: {str(error)[:240]}"
        ) from error
    finally:
        connection.close()
    latency_ms = round((time.perf_counter() - start) * 1000, 3)
    return QueryExecutionResult(columns=columns, rows=rows, row_count=len(rows), latency_ms=latency_ms)
