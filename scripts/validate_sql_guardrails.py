from __future__ import annotations

import sys
import os
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import inspect


ROOT = Path(__file__).resolve().parents[1]
BACKEND_PATH = ROOT / "backend"
if str(BACKEND_PATH) not in sys.path:
    sys.path.insert(0, str(BACKEND_PATH))

from app.services.sql_guardrails import (
    InputOutOfScopeError,
    SQLGuardrailError,
    validate_analytics_question,
    validate_and_sanitize_sql,
)
from app.database import create_database_engine
from app.config import DEFAULT_MAX_ROWS, get_max_rows
from app.schemas.linking import LinkableColumn, LinkableTable
from app.services.example_retriever import load_example_library, retrieve_examples
from app.services.schema_linker import link_schema
from validation_helpers import check


SCHEMA = {
    "products": ["product_id", "product_name", "unit_price_cents"],
    "customers": ["customer_id", "full_name", "email"],
}



def expect_error(action, error_type: type[Exception], message: str) -> None:
    try:
        action()
    except error_type:
        check(True, message)
    else:
        raise AssertionError(message)


def validate_input_guardrails() -> None:
    expect_error(
        lambda: validate_analytics_question(
            "give me python code to write READ only SQL",
            SCHEMA,
        ),
        InputOutOfScopeError,
        "Python/code requests are rejected before calling the LLM",
    )
    expect_error(
        lambda: validate_analytics_question("Tell me a joke", SCHEMA),
        InputOutOfScopeError,
        "unrelated conversational requests are rejected",
    )
    validate_analytics_question("List product names", SCHEMA)
    check(True, "schema-relevant analytics requests are accepted")


def validate_row_limit_configuration() -> None:
    with patch.dict(os.environ, {"QUERYMIND_MAX_ROWS": "37"}):
        check(get_max_rows() == 37, "configured row limit is used")
    with patch.dict(os.environ, {"QUERYMIND_MAX_ROWS": "not-a-number"}):
        check(get_max_rows() == DEFAULT_MAX_ROWS, "invalid row-limit setting falls back to the default")
    with patch.dict(os.environ, {"QUERYMIND_MAX_ROWS": "0"}):
        check(get_max_rows() == 1, "row-limit setting is clamped to at least one")
    with patch.dict(os.environ, {"QUERYMIND_MAX_ROWS": "5000"}):
        check(get_max_rows() == 1000, "row-limit setting is capped at one thousand")


def validate_output_guardrails() -> None:
    def validate(sql: str, *, max_rows: int = 25) -> str:
        return validate_and_sanitize_sql(
            sql=sql,
            dialect="sqlite",
            question="List product names",
            schema_catalog=SCHEMA,
            relevant_tables=["products"],
            relevant_columns=[("products", "product_name")],
            examples=[],
            max_rows=max_rows,
        )

    safe_sql = validate("SELECT p.product_name FROM products AS p")
    check("LIMIT 25" in safe_sql.upper(), "missing row limit is added")

    reduced_limit = validate("SELECT product_name FROM products LIMIT 500", max_rows=25)
    check("LIMIT 25" in reduced_limit.upper(), "excessive row limit is reduced to configured maximum")

    retained_limit = validate("SELECT product_name FROM products LIMIT 10", max_rows=25)
    check("LIMIT 10" in retained_limit.upper(), "a smaller existing row limit is preserved")

    malformed_limit = validate("SELECT product_name FROM products LIMIT (SELECT 999)", max_rows=25)
    check("LIMIT 25" in malformed_limit.upper(), "non-literal limit expressions are replaced with the configured cap")

    count_sql = validate("SELECT COUNT(*) FROM products")
    check("COUNT(*)" in count_sql.upper(), "COUNT(*) remains allowed as an aggregate expression")
    check(
        "LIKE LOWER('%TOY%')" in validate(
            "SELECT product_name FROM products WHERE LOWER(product_name) LIKE LOWER('%TOY%')"
        ).upper(),
        "case-insensitive LIKE filters on text columns are allowed",
    )
    projection_fallback_sql = validate_and_sanitize_sql(
        sql="SELECT DISTINCT p.product_name FROM products AS p",
        dialect="sqlite",
        question="what all product purchased by Priya Patel",
        schema_catalog=SCHEMA,
        relevant_tables=["products"],
        relevant_columns=[],
        examples=[],
        max_rows=25,
    )
    check(
        "PRODUCT_NAME" in projection_fallback_sql.upper(),
        "linked-table projection fallback allows valid columns when linker resolves no columns",
    )
    join_fallback_sql = validate_and_sanitize_sql(
        sql=(
            "SELECT p.product_name, COUNT(*) AS purchase_count "
            "FROM customers c "
            "JOIN orders o ON o.customer_id = c.customer_id "
            "JOIN order_items oi ON oi.order_id = o.order_id "
            "JOIN products p ON p.product_id = oi.product_id "
            "WHERE LOWER(c.full_name) LIKE '%priya patel%' "
            "GROUP BY p.product_name"
        ),
        dialect="sqlite",
        question="give me product name and count of product purchased by Priya Patel",
        schema_catalog={
            "customers": ["customer_id", "full_name"],
            "orders": ["order_id", "customer_id"],
            "order_items": ["order_id", "product_id"],
            "products": ["product_id", "product_name"],
        },
        relevant_tables=["products"],
        relevant_columns=[],
        examples=[],
        max_rows=25,
    )
    check(
        "PURCHASE_COUNT" in join_fallback_sql.upper(),
        "linked-table join fallback allows schema-valid bridge joins when linker resolves only target table",
    )

    expect_error(lambda: validate("SELECT * FROM products"), SQLGuardrailError, "wildcard selection is rejected")
    expect_error(
        lambda: validate("SELECT p.* FROM products AS p"),
        SQLGuardrailError,
        "qualified wildcard selection is rejected",
    )
    expect_error(
        lambda: validate("DELETE FROM products"),
        SQLGuardrailError,
        "destructive SQL is rejected",
    )
    expect_error(
        lambda: validate("SELECT product_name FROM products; DROP TABLE customers"),
        SQLGuardrailError,
        "multiple statements are rejected",
    )
    expect_error(
        lambda: validate(
            "WITH removed AS (DELETE FROM products RETURNING product_id) "
            "SELECT product_id FROM removed"
        ),
        SQLGuardrailError,
        "data-changing statements hidden in a CTE are rejected",
    )
    expect_error(
        lambda: validate("SELECT full_name FROM customers"),
        SQLGuardrailError,
        "valid but question-unrelated tables are rejected",
    )
    expect_error(
        lambda: validate("SELECT email FROM products"),
        SQLGuardrailError,
        "columns that do not exist in the referenced table are rejected",
    )
    expect_error(
        lambda: validate("SELECT email FROM products"),
        SQLGuardrailError,
        "unrequested columns are rejected even when the SQL table is relevant",
    )
    expect_error(
        lambda: validate("SELECT nonexistent FROM products"),
        SQLGuardrailError,
        "unknown columns are rejected",
    )
    expect_error(
        lambda: validate("SELECT full_name FROM products"),
        SQLGuardrailError,
        "unqualified columns from unrelated tables are rejected",
    )
    expect_error(
        lambda: validate("SELECT product_name FROM ghost_products"),
        SQLGuardrailError,
        "unknown tables are rejected",
    )
    normalized_from_equals = validate("SELECT product_name FROM products WHERE product_name = 'toy'")
    check(
        "LOWER(PRODUCT_NAME) LIKE LOWER('%TOY%')" in normalized_from_equals.upper(),
        "text equality filters are normalized to case-insensitive partial matching",
    )
    normalized_from_like = validate("SELECT product_name FROM products WHERE product_name LIKE '%toy%'")
    check(
        "LOWER(PRODUCT_NAME) LIKE LOWER('%TOY%')" in normalized_from_like.upper(),
        "text LIKE filters without case normalization are upgraded automatically",
    )
    exact_match_sql = validate_and_sanitize_sql(
        sql="SELECT product_name FROM products WHERE product_name = 'Toys Classic 0008'",
        dialect="sqlite",
        question="Give exact product name Toys Classic 0008",
        schema_catalog=SCHEMA,
        relevant_tables=["products"],
        relevant_columns=[("products", "product_name")],
        examples=[],
        max_rows=25,
    )
    check(
        "PRODUCT_NAME = 'TOYS CLASSIC 0008'" in exact_match_sql.upper(),
        "text equality filters are allowed when the question explicitly requests exact matching",
    )
    expect_error(
        lambda: validate("SELECT 1"),
        SQLGuardrailError,
        "queries without a configured table source are rejected",
    )
    expect_error(
        lambda: validate_and_sanitize_sql(
            sql="SELECT id FROM products JOIN customers ON 1 = 1",
            dialect="sqlite",
            question="List products and customers",
            schema_catalog={
                "products": ["id", "product_name"],
                "customers": ["id", "full_name"],
            },
            relevant_tables=["products", "customers"],
            relevant_columns=[("products", "product_name"), ("customers", "full_name")],
            examples=[],
            max_rows=25,
        ),
        SQLGuardrailError,
        "unqualified columns shared by joined tables are rejected as ambiguous",
    )

    expect_error(
        lambda: validate_and_sanitize_sql(
            sql="SELECT email FROM customers",
            dialect="sqlite",
            question="List customer names",
            schema_catalog={"customers": ["customer_id", "full_name", "email"]},
            relevant_tables=["customers"],
            relevant_columns=[("customers", "full_name")],
            examples=[],
            max_rows=25,
        ),
        SQLGuardrailError,
        "valid PII columns are rejected when not requested by the question",
    )


def validate_curated_example_sql() -> None:
    engine = create_database_engine()
    try:
        inspector = inspect(engine)
        schema_name = inspector.default_schema_name
        table_names = inspector.get_table_names(schema=schema_name)
        schema_catalog = {
            table_name: [
                column["name"]
                for column in inspector.get_columns(table_name, schema=schema_name)
            ]
            for table_name in table_names
        }
        linkable_schema = [
            LinkableTable(
                name=table_name,
                columns=[LinkableColumn(name=column_name) for column_name in column_names],
            )
            for table_name, column_names in schema_catalog.items()
        ]
        examples = load_example_library()

        for example in examples:
            linked = link_schema(example.question, linkable_schema)
            retrieved = retrieve_examples(example.question, examples, top_k=3)
            validate_and_sanitize_sql(
                sql=example.sql,
                dialect=engine.dialect.name,
                question=example.question,
                schema_catalog=schema_catalog,
                relevant_tables=linked.resolved_tables,
                relevant_columns=[
                    (column.table_name, column.column_name)
                    for column in linked.resolved_columns
                ],
                examples=retrieved,
                max_rows=100,
            )
            check(True, f"curated SQL passes schema and relevance guardrails: {example.id}")
    finally:
        engine.dispose()


def main() -> None:
    validate_input_guardrails()
    validate_row_limit_configuration()
    validate_output_guardrails()
    validate_curated_example_sql()
    print("SQL guardrail validation passed.")


if __name__ == "__main__":
    main()