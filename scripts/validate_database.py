from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
from validation_helpers import check


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = ROOT / "data" / "querymind.sqlite3"
MINIMUM_PRIMARY_ROWS = 1000
PRIMARY_TABLES = ("customers", "products", "orders", "order_items", "payments", "shipments")



def validate_database(db_path: Path) -> None:
    check(db_path.is_file(), f"database exists at {db_path}")

    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")

        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        expected_tables = {
            "categories",
            "customers",
            "products",
            "orders",
            "order_items",
            "payments",
            "shipments",
        }
        check(expected_tables <= tables, "all seven required tables exist")

        row_counts = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in expected_tables
        }
        check(
            all(row_counts[table] >= MINIMUM_PRIMARY_ROWS for table in PRIMARY_TABLES),
            "each primary table has at least 1,000 rows: "
            + ", ".join(f"{table}={row_counts[table]:,}" for table in PRIMARY_TABLES),
        )
        check(row_counts["categories"] >= 6, "category lookup has at least six categories")

        foreign_key_errors = list(connection.execute("PRAGMA foreign_key_check"))
        check(not foreign_key_errors, "all foreign-key relationships are valid")

        inconsistent_orders = connection.execute(
            """
            SELECT COUNT(*)
            FROM orders AS o
            LEFT JOIN (
                SELECT order_id, SUM(line_total_cents) AS item_total_cents
                FROM order_items
                GROUP BY order_id
            ) AS items ON items.order_id = o.order_id
            WHERE items.item_total_cents IS NULL
               OR items.item_total_cents != o.total_amount_cents
            """
        ).fetchone()[0]
        check(inconsistent_orders == 0, "order totals equal the sum of their line items")

        missing_category_products = connection.execute(
            "SELECT COUNT(*) FROM products AS p LEFT JOIN categories AS c USING (category_id) WHERE c.category_id IS NULL"
        ).fetchone()[0]
        check(missing_category_products == 0, "every product belongs to a category")

        invalid_shipments = connection.execute(
            """
            SELECT COUNT(*)
            FROM shipments AS s
            JOIN orders AS o USING (order_id)
            WHERE o.status NOT IN ('shipped', 'delivered')
            """
        ).fetchone()[0]
        check(invalid_shipments == 0, "shipments belong only to shipped or delivered orders")

        date_fields = (
            ("customers", "signup_date"),
            ("products", "created_at"),
            ("orders", "order_date"),
            ("payments", "paid_at"),
            ("shipments", "shipped_at"),
        )
        check(len(date_fields) >= 2, "schema includes multiple temporal fields")
        for table, column in date_fields:
            invalid_dates = connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {column} IS NULL OR julianday({column}) IS NULL"
            ).fetchone()[0]
            check(invalid_dates == 0, f"{table}.{column} contains valid timestamps")

        ambiguity_columns = {
            (table, row["name"])
            for table in ("orders", "payments")
            for row in connection.execute(f"PRAGMA table_info({table})")
        }
        check(
            ("orders", "total_amount_cents") in ambiguity_columns
            and ("payments", "amount_cents") in ambiguity_columns,
            "order totals and payment amounts provide a documented revenue ambiguity case",
        )

        aggregate_queries = {
            "SUM by product": """
                SELECT p.product_id, SUM(oi.line_total_cents) AS gross_revenue_cents
                FROM order_items AS oi JOIN products AS p USING (product_id)
                GROUP BY p.product_id ORDER BY gross_revenue_cents DESC LIMIT 5
            """,
            "COUNT by customer region": """
                SELECT c.region, COUNT(o.order_id) AS order_count
                FROM customers AS c JOIN orders AS o USING (customer_id)
                GROUP BY c.region
            """,
            "AVG by order status": """
                SELECT status, AVG(total_amount_cents) AS average_order_cents
                FROM orders GROUP BY status
            """,
            "monthly temporal revenue": """
                SELECT strftime('%Y-%m', o.order_date) AS order_month,
                       SUM(oi.line_total_cents) AS gross_revenue_cents
                FROM orders AS o JOIN order_items AS oi USING (order_id)
                GROUP BY order_month ORDER BY order_month
            """,
        }
        for name, query in aggregate_queries.items():
            result_count = len(list(connection.execute(query)))
            check(result_count > 0, f"representative {name} query returns rows")

    print("Database validation passed.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate the SQLite demo database.")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args()
    validate_database(args.db_path.resolve())


if __name__ == "__main__":
    main()