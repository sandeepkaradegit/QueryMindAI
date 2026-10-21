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
from app.schemas.linking import LinkableColumn, LinkableTable
from app.services.schema_linker import link_schema
from validation_helpers import check



def demo_linkable_schema() -> list[LinkableTable]:
    return [
        LinkableTable(
            name="customers",
            description="People who purchase products",
            synonyms=["shoppers", "buyers"],
            columns=[
                LinkableColumn(name="customer_id"),
                LinkableColumn(name="full_name", synonyms=["customer name", "shopper name"]),
                LinkableColumn(name="region", description="Geographic sales area"),
            ],
        ),
        LinkableTable(
            name="orders",
            description="Purchase records",
            synonyms=["purchases"],
            columns=[
                LinkableColumn(name="order_id"),
                LinkableColumn(name="total_amount_cents", synonyms=["amount", "order amount"]),
            ],
        ),
        LinkableTable(
            name="payments",
            description="Money transferred for purchases",
            synonyms=["transactions"],
            columns=[
                LinkableColumn(name="payment_id"),
                LinkableColumn(name="amount_cents", synonyms=["amount", "payment amount"]),
            ],
        ),
    ]


def validate_direct_linking() -> None:
    schema = demo_linkable_schema()
    unambiguous = link_schema("List shopper names by purchase amount", schema)
    check(unambiguous.resolved_tables == ["customers", "orders"], "synonyms resolve tables")
    check(
        [(column.table_name, column.column_name) for column in unambiguous.resolved_columns]
        == [("customers", "full_name"), ("orders", "total_amount_cents")],
        "table context resolves a column synonym to the relevant table",
    )
    check(not unambiguous.ambiguities, "table context prevents a false amount ambiguity")

    ambiguous = link_schema("Show amount", schema)
    check(not ambiguous.resolved_columns, "ambiguous columns are not silently selected")
    check(len(ambiguous.ambiguities) == 1, "same synonym across tables creates one ambiguity")
    check(
        {(candidate.table_name, candidate.column_name) for candidate in ambiguous.ambiguities[0].candidates}
        == {("orders", "total_amount_cents"), ("payments", "amount_cents")},
        "ambiguity lists every matching column candidate",
    )

    described = link_schema("Show the geographic area", schema)
    check(
        any(column.column_name == "region" for column in described.resolved_columns),
        "column descriptions contribute searchable terms",
    )


async def validate_api_linking() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE billing (billing_id INTEGER PRIMARY KEY, amount INTEGER NOT NULL)"
            )
            connection.exec_driver_sql(
                "CREATE TABLE claims (claim_id INTEGER PRIMARY KEY, amount INTEGER NOT NULL)"
            )

        app.dependency_overrides[get_database_engine] = lambda: engine
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post("/query/link", json={"question": "Show amount"})
        check(response.status_code == 200, "schema-link API responds successfully")
        payload = response.json()
        check(payload["resolved_columns"] == [], "API does not guess between identically named columns")
        check(
            {candidate["table_name"] for candidate in payload["ambiguities"][0]["candidates"]}
            == {"billing", "claims"},
            "API reports ambiguity candidates from a non-demo schema",
        )
    finally:
        app.dependency_overrides.pop(get_database_engine, None)
        engine.dispose()


def main() -> None:
    validate_direct_linking()
    asyncio.run(validate_api_linking())
    print("Schema linker validation passed.")


if __name__ == "__main__":
    main()