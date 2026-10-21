from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool


ROOT = Path(__file__).resolve().parents[1]
BACKEND_PATH = ROOT / "backend"
if str(BACKEND_PATH) not in sys.path:
    sys.path.insert(0, str(BACKEND_PATH))

from app.api.routes import get_safety_guardrail, get_sql_completion_provider
from app.database import create_database_engine, get_database_engine, DEFAULT_DB_PATH
from app.main import app
from app.auth import get_current_user
from app import admin_store

import os, tempfile
os.environ["QUERYMIND_ADMIN_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "admin.sqlite3")
app.dependency_overrides[get_current_user] = lambda: admin_store.AdminUserRecord(
    id=1, username="test-admin", password_hash="", password_salt="", role="admin", created_at=""
)
from app.services.openevals_guardrails import GuardrailVerdict
from app.services.result_formatter import format_query_result
from app.services.sql_executor import (
from validation_helpers import check
    QueryExecutionError,
    QueryExecutionResult,
    QueryExecutionTimeoutError,
    execute_validated_sql,
)



class FakeProvider:
    def __init__(self, response: str) -> None:
        self.response = response

    def complete(self, messages: list[dict[str, str]], model: str) -> str:
        return self.response


class FakeSafetyGuardrail:
    def evaluate_input(self, question, schema_catalog):
        return GuardrailVerdict(verdict="allow", reason_code="none")

    def evaluate_output(self, question, sql, explanation, schema_catalog):
        return GuardrailVerdict(verdict="allow", reason_code="none")


def validate_executor() -> None:
    engine = create_database_engine(f"sqlite:///{DEFAULT_DB_PATH.as_posix()}")
    try:
        result = execute_validated_sql(
            engine,
            "SELECT status, COUNT(*) AS order_count FROM orders GROUP BY status",
            max_rows=100,
            timeout_seconds=5.0,
        )
        check(result.columns == ["status", "order_count"], "executor reports selected column names")
        check(result.row_count == len(result.rows) and result.row_count > 0, "executor reports a matching row count")
        check(result.latency_ms >= 0, "executor reports non-negative latency")

        capped = execute_validated_sql(
            engine,
            "SELECT customer_id FROM customers",
            max_rows=3,
            timeout_seconds=5.0,
        )
        check(capped.row_count == 3, "executor enforces the configured row cap even without a SQL LIMIT clause")

        try:
            execute_validated_sql(engine, "DELETE FROM customers", max_rows=100, timeout_seconds=5.0)
        except QueryExecutionError:
            check(True, "executor rejects write statements at the connection level as defense in depth")
        else:
            raise AssertionError("Write statement was executed against the read-only connection")

        try:
            execute_validated_sql(
                engine,
                "WITH RECURSIVE counter(x) AS ("
                "SELECT 1 UNION ALL SELECT x + 1 FROM counter WHERE x < 100000000"
                ") SELECT COUNT(*) FROM counter",
                max_rows=100,
                timeout_seconds=0.05,
            )
        except QueryExecutionTimeoutError:
            check(True, "executor enforces the configured execution timeout")
        else:
            raise AssertionError("Slow query was not stopped by the execution timeout")
    finally:
        engine.dispose()


def validate_formatter() -> None:
    empty = QueryExecutionResult(columns=["product_name"], rows=[], row_count=0, latency_ms=1.0)
    _, summary = format_query_result(empty, question="List product names")
    check("No rows matched" in summary, "formatter summarizes an empty result set")

    single_cell = QueryExecutionResult(columns=["order_count"], rows=[[42]], row_count=1, latency_ms=1.0)
    table, summary = format_query_result(single_cell, question="How many orders are there?")
    check(table == [{"order_count": 42}], "formatter converts a single-cell result into a table row")
    check("order count: 42" in summary, "formatter summarizes a single-cell result directly")

    multi_row = QueryExecutionResult(
        columns=["product_name", "gross_revenue_cents"],
        rows=[["Widget", 100], ["Gadget", 50]],
        row_count=2,
        latency_ms=1.0,
    )
    table, summary = format_query_result(multi_row, question="Top products by revenue")
    check(
        table == [
            {"product_name": "Widget", "gross_revenue_cents": 100},
            {"product_name": "Gadget", "gross_revenue_cents": 50},
        ],
        "formatter converts multi-row results into a list of row dicts",
    )
    check("Returned 2 rows across 2 columns" in summary, "formatter summarizes a multi-row result")


async def validate_api() -> None:
    fake_provider = FakeProvider(
        json.dumps(
            {
                "sql": "SELECT p.product_name, SUM(oi.line_total_cents) AS gross_revenue_cents "
                "FROM order_items AS oi JOIN products AS p USING (product_id) "
                "GROUP BY p.product_id, p.product_name ORDER BY gross_revenue_cents DESC LIMIT 10",
                "explanation": "Lists the top 10 products by gross revenue.",
            }
        )
    )
    app.dependency_overrides[get_sql_completion_provider] = lambda: lambda: fake_provider
    app.dependency_overrides[get_safety_guardrail] = lambda: lambda: FakeSafetyGuardrail()
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/query",
                json={"question": "What are the top 10 products by gross revenue?"},
            )
            check(response.status_code == 200, "the connected query pipeline executes end to end")
            body = response.json()
            check(body["status"] == "ok", "successful query response reports status ok")
            check(body["sql"].startswith("SELECT"), "response includes the validated SQL")
            check(body["columns"] == ["product_name", "gross_revenue_cents"], "response includes result columns")
            check(body["row_count"] == len(body["rows"]), "response row count matches the returned rows")
            check(body["row_count"] > 0, "response contains executed rows, not just generated SQL")
            check(body["latency_ms"] >= 0, "response reports execution latency")
            check(bool(body["nl_summary"]), "response includes a natural-language summary")
            check("X-QueryMind-Trace-Id" in response.headers, "query response is traced")

            destructive = await client.post("/query", json={"question": "Delete all orders"})
            check(destructive.status_code == 422, "destructive requests are still refused before execution")

            off_topic = await client.post("/query", json={"question": "What is the weather in London today?"})
            check(off_topic.status_code == 422, "off-topic requests are still refused before execution")

            app.dependency_overrides[get_safety_guardrail] = lambda: lambda: _BlockingGuardrail()
            blocked = await client.post("/query", json={"question": "List product names"})
            check(blocked.status_code == 422, "safety-judge blocks still prevent execution")
            app.dependency_overrides[get_safety_guardrail] = lambda: lambda: FakeSafetyGuardrail()

            ambiguous_engine = create_engine(
                "sqlite://",
                connect_args={"check_same_thread": False},
                poolclass=StaticPool,
            )
            with ambiguous_engine.begin() as connection:
                connection.exec_driver_sql("CREATE TABLE billing (billing_id INTEGER PRIMARY KEY, amount INTEGER)")
                connection.exec_driver_sql("CREATE TABLE claims (claim_id INTEGER PRIMARY KEY, amount INTEGER)")
            app.dependency_overrides[get_database_engine] = lambda: ambiguous_engine
            try:
                ambiguous = await client.post("/query", json={"question": "Show amount"})
                check(ambiguous.status_code == 409, "ambiguous schema terms still require clarification before execution")
            finally:
                app.dependency_overrides.pop(get_database_engine, None)
                ambiguous_engine.dispose()
    finally:
        app.dependency_overrides.pop(get_sql_completion_provider, None)
        app.dependency_overrides.pop(get_safety_guardrail, None)


class _BlockingGuardrail:
    def evaluate_input(self, question, schema_catalog):
        return GuardrailVerdict(verdict="block", reason_code="prompt_injection")

    def evaluate_output(self, question, sql, explanation, schema_catalog):
        return GuardrailVerdict(verdict="allow", reason_code="none")


def main() -> None:
    validate_executor()
    validate_formatter()
    asyncio.run(validate_api())
    print("Query pipeline validation passed.")


if __name__ == "__main__":
    main()
