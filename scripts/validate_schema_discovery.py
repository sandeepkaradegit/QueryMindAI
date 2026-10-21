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
from validation_helpers import check
os.environ["QUERYMIND_ADMIN_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "admin.sqlite3")
app.dependency_overrides[get_current_user] = lambda: admin_store.AdminUserRecord(
    id=1, username="test-admin", password_hash="", password_salt="", role="admin", created_at=""
)



async def get_schema(client: httpx.AsyncClient) -> dict:
    response = await client.get("/admin/schema")
    check(response.status_code == 200, "schema endpoint responds successfully")
    return response.json()


async def validate_demo_schema(client: httpx.AsyncClient) -> None:
    schema = await get_schema(client)
    tables_by_name = {table["name"]: table for table in schema["tables"]}
    check(schema["dialect"] == "sqlite", "demo database dialect is identified")
    check(
        {"categories", "customers", "products", "orders", "order_items", "payments", "shipments"}
        <= tables_by_name.keys(),
        "schema discovery returns the demo database tables",
    )
    customer_columns = {column["name"]: column for column in tables_by_name["customers"]["columns"]}
    check(
        customer_columns["customer_id"]["primary_key"]
        and not customer_columns["customer_id"]["nullable"],
        "primary-key metadata is reported accurately",
    )
    order_item_foreign_keys = tables_by_name["order_items"]["foreign_keys"]
    check(
        any(foreign_key["referred_table"] == "orders" for foreign_key in order_item_foreign_keys),
        "foreign-key metadata is reported for demo relationships",
    )


async def validate_independent_schema(client: httpx.AsyncClient) -> None:
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
        schema = await get_schema(client)
        tables_by_name = {table["name"]: table for table in schema["tables"]}
        check(
            set(tables_by_name) == {"departments", "employees"},
            "a different domain schema is discovered without demo-table assumptions",
        )
        employee = tables_by_name["employees"]
        check(employee["schema"] == "main", "table schema name is included in the response")
        check(
            any(foreign_key["referred_table"] == "departments" for foreign_key in employee["foreign_keys"]),
            "foreign keys in an independent schema are discovered",
        )
    finally:
        app.dependency_overrides.pop(get_database_engine, None)
        engine.dispose()


async def validate_schema_api() -> None:
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            await validate_demo_schema(client)
            await validate_independent_schema(client)
    finally:
        pass


def main() -> None:
    asyncio.run(validate_schema_api())
    print("Schema discovery validation passed.")


if __name__ == "__main__":
    main()