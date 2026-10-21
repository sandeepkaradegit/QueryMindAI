from __future__ import annotations

import argparse
import csv
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = ROOT / "data" / "querymind.sqlite3"


def _read_sql(args: argparse.Namespace) -> str:
    if args.sql:
        return args.sql.strip()
    if args.sql_file:
        return args.sql_file.read_text(encoding="utf-8").strip()
    raise ValueError("Provide either --sql or --sql-file.")


def _validate_read_only_query(sql: str) -> None:
    if not sql:
        raise ValueError("SQL must not be empty.")
    normalized = sql.strip().rstrip(";").lstrip()
    if ";" in normalized:
        raise ValueError("Only one SQL statement is allowed.")
    upper = normalized.upper()
    if not (upper.startswith("SELECT ") or upper.startswith("WITH ")):
        raise ValueError("Only read-only SELECT/WITH queries are allowed.")


def _print_table(columns: list[str], rows: list[sqlite3.Row]) -> None:
    if not rows:
        print("No rows returned.")
        return
    widths = [len(column) for column in columns]
    for row in rows:
        for index, column in enumerate(columns):
            widths[index] = max(widths[index], len(str(row[column])))

    def _line(values: list[str]) -> str:
        return " | ".join(value.ljust(widths[index]) for index, value in enumerate(values))

    print(_line(columns))
    print("-+-".join("-" * width for width in widths))
    for row in rows:
        print(_line([str(row[column]) for column in columns]))


def run_verification(
    *,
    db_path: Path,
    sql: str,
    output_csv: Path | None,
    max_rows_display: int,
) -> None:
    if not db_path.is_file():
        raise FileNotFoundError(f"Database file not found: {db_path}")
    _validate_read_only_query(sql)

    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        cursor = connection.execute(sql)
        columns = [column_info[0] for column_info in cursor.description] if cursor.description else []
        rows = cursor.fetchall()

    print(f"Database: {db_path}")
    print(f"Rows returned: {len(rows)}")
    if columns:
        print(f"Columns: {', '.join(columns)}")
    print()

    display_rows = rows[:max_rows_display]
    _print_table(columns, display_rows)
    if len(rows) > max_rows_display:
        print(f"\nShowing first {max_rows_display} rows out of {len(rows)} total.")

    if output_csv:
        output_csv.parent.mkdir(parents=True, exist_ok=True)
        with output_csv.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(columns)
            for row in rows:
                writer.writerow([row[column] for column in columns])
        print(f"Saved full result set to: {output_csv}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a read-only SQL query against querymind.sqlite3 for cross-verification."
    )
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH, help="Path to the SQLite database.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--sql", type=str, help="SQL query string to execute.")
    group.add_argument("--sql-file", type=Path, help="Path to a .sql file containing one query.")
    parser.add_argument(
        "--output-csv",
        type=Path,
        default=None,
        help="Optional path to save the full query result as CSV.",
    )
    parser.add_argument(
        "--max-rows-display",
        type=int,
        default=50,
        help="Maximum number of rows to print in the terminal (default: 50).",
    )
    args = parser.parse_args()

    if args.max_rows_display < 1:
        raise ValueError("--max-rows-display must be at least 1.")

    sql = _read_sql(args)
    run_verification(
        db_path=args.db_path.resolve(),
        sql=sql,
        output_csv=args.output_csv.resolve() if args.output_csv else None,
        max_rows_display=args.max_rows_display,
    )


if __name__ == "__main__":
    main()
