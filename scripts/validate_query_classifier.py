from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool


ROOT = Path(__file__).resolve().parents[1]
BACKEND_PATH = ROOT / "backend"
if str(BACKEND_PATH) not in sys.path:
    sys.path.insert(0, str(BACKEND_PATH))

from app.database import get_database_engine
from app.main import app
from app.auth import get_current_user
from app import admin_store

import os, tempfile
os.environ["QUERYMIND_ADMIN_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "admin.sqlite3")
app.dependency_overrides[get_current_user] = lambda: admin_store.AdminUserRecord(
    id=1, username="test-admin", password_hash="", password_salt="", role="admin", created_at=""
)
from app.schemas.classification import QueryType
from app.services.query_classifier import classify_question
from validation_helpers import check



def validate_classifier_categories() -> None:
    catalog = {
        "customers": ["customer_id", "full_name", "email", "region"],
        "orders": ["order_id", "customer_id", "order_date", "total_amount_cents", "status"],
        "products": ["product_id", "product_name", "unit_price_cents"],
    }
    cases = (
        ("Show product names", QueryType.SELECT_SIMPLE),
        ("Count orders by status", QueryType.SELECT_AGGREGATE),
        ("List customers and their orders", QueryType.SELECT_JOIN),
        ("How many orders were placed last month?", QueryType.SELECT_TEMPORAL),
        ("Show customers by last name", QueryType.SELECT_SIMPLE),
        ("Delete all customer records", QueryType.UNSUPPORTED),
        ("Create a summary of customer emails", QueryType.SELECT_SIMPLE),
        ("CREATE TABLE archive (id INTEGER)", QueryType.UNSUPPORTED),
        ("DROP TABLE customers", QueryType.UNSUPPORTED),
    )

    for question, expected_type in cases:
        result = classify_question(question, catalog)
        check(result.query_type == expected_type, f"{expected_type.value}: {question}")

    mentioned = classify_question("Show customers by email", catalog)
    check(mentioned.tables_mentioned == ["customers"], "mentioned tables resolve against supplied schema names")
    check(mentioned.columns_mentioned == ["email"], "mentioned columns resolve against supplied schema names")


async def validate_classifier_api() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE departments (department_id INTEGER PRIMARY KEY, name TEXT NOT NULL)"
            )
            connection.exec_driver_sql(
                "CREATE TABLE employees (employee_id INTEGER PRIMARY KEY, department_id INTEGER "
                "REFERENCES departments(department_id), full_name TEXT NOT NULL)"
            )

        app.dependency_overrides[get_database_engine] = lambda: engine
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/query/classify",
                json={"question": "List employees with their departments"},
            )
            check(response.status_code == 200, "classifier API responds successfully")
            result = response.json()
            check(result["query_type"] == QueryType.SELECT_JOIN.value, "API returns the typed join classification")
            check(
                result["tables_mentioned"] == ["departments", "employees"],
                "API resolves mentioned tables from the configured non-demo schema",
            )

            unsupported = await client.post(
                "/query/classify",
                json={"question": "Delete all employees"},
            )
            check(
                unsupported.json()["query_type"] == QueryType.UNSUPPORTED.value,
                "API marks destructive intent unsupported",
            )
    finally:
        app.dependency_overrides.pop(get_database_engine, None)
        engine.dispose()


def main() -> None:
    validate_classifier_categories()
    asyncio.run(validate_classifier_api())
    print("Query classifier validation passed.")


if __name__ == "__main__":
    main()