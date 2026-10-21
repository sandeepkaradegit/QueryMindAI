from __future__ import annotations

from app.services.sql_executor import QueryExecutionResult


def format_query_result(
    execution: QueryExecutionResult,
    *,
    question: str,
) -> tuple[list[dict[str, object]], str]:
    table = [dict(zip(execution.columns, row, strict=True)) for row in execution.rows]

    if not table:
        summary = f'No rows matched "{question}".'
    elif len(execution.columns) == 1 and len(table) == 1:
        column_label = execution.columns[0].replace("_", " ")
        summary = f"{column_label}: {table[0][execution.columns[0]]}."
    else:
        row_word = "row" if execution.row_count == 1 else "rows"
        column_word = "column" if len(execution.columns) == 1 else "columns"
        summary = f"Returned {execution.row_count} {row_word} across {len(execution.columns)} {column_word}."

    return table, summary
