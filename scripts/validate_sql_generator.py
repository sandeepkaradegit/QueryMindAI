from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import httpx
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool


ROOT = Path(__file__).resolve().parents[1]
BACKEND_PATH = ROOT / "backend"
if str(BACKEND_PATH) not in sys.path:
    sys.path.insert(0, str(BACKEND_PATH))

from app.api.routes import get_safety_guardrail, get_sql_completion_provider
from app.database import get_database_engine
from app.config import get_groq_model
from app.main import app
from app.auth import get_current_user
from app import admin_store

import os, tempfile
os.environ["QUERYMIND_ADMIN_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "admin.sqlite3")
os.environ["QUERYMIND_EXAMPLE_RETRIEVAL"] = "lexical"  # deterministic and offline; vector search has its own validator
app.dependency_overrides[get_current_user] = lambda: admin_store.AdminUserRecord(
    id=1, username="test-admin", password_hash="", password_salt="", role="admin", created_at=""
)
from app.schemas.classification import QueryType
from app.schemas.examples import QuestionSqlExample
from app.services.sql_generator import UnsupportedQuestionError, generate_sql
from app.services.openevals_guardrails import GuardrailVerdict
from validation_helpers import check


class FakeProvider:
    def __init__(self, response: str) -> None:
        self.response = response
        self.messages: list[dict[str, str]] | None = None
        self.model: str | None = None

    def complete(self, messages: list[dict[str, str]], model: str) -> str:
        self.messages = messages
        self.model = model
        return self.response


class FakeSafetyGuardrail:
    def evaluate_input(self, question, schema_catalog):
        return GuardrailVerdict(verdict="allow", reason_code="none")

    def evaluate_output(self, question, sql, explanation, schema_catalog):
        return GuardrailVerdict(verdict="allow", reason_code="none")



def validate_generator_service() -> None:
    schema = {
        "orders": ["order_id", "order_date", "total_amount_cents", "customer_id"],
        "order_items": ["order_id", "product_id", "line_total_cents"],
        "products": ["product_id", "product_name"],
    }
    examples = [
        QuestionSqlExample(
            id="top-products",
            question="What are the top products by gross revenue?",
            sql="SELECT product_id, SUM(line_total_cents) FROM order_items GROUP BY product_id ORDER BY 2 DESC LIMIT 10",
            query_type=QueryType.SELECT_AGGREGATE,
        ),
        QuestionSqlExample(
            id="orders-by-status",
            question="Count orders by status",
            sql="SELECT status, COUNT(*) FROM orders GROUP BY status",
            query_type=QueryType.SELECT_AGGREGATE,
        ),
    ]
    provider = FakeProvider(
        json.dumps(
            {
                "sql": "SELECT p.product_name FROM products AS p LIMIT 10",
                "explanation": "Returns up to ten product names.",
            }
        )
    )
    result = generate_sql(
        question="List product names",
        dialect="sqlite",
        schema_catalog=schema,
        examples=examples,
        provider_factory=lambda: provider,
        model="test-model",
        schema_relationships=[
            {
                "table_name": "orders",
                "columns": ["customer_id"],
                "referred_table": "customers",
                "referred_columns": ["customer_id"],
            }
        ],
    )
    check(result.sql.startswith("SELECT"), "generator returns the provider SQL")
    check(result.model == "test-model", "response identifies the configured model")
    check(result.query_type == QueryType.SELECT_SIMPLE, "response includes the classifier result")
    check(provider.model == "test-model", "configured model is passed to the provider")
    check(provider.messages is not None and len(provider.messages) == 2, "provider receives system and user messages")
    assert provider.messages is not None
    prompt_context = json.loads(provider.messages[1]["content"])
    check(prompt_context["dialect"] == "sqlite", "prompt includes the SQL dialect")
    check(prompt_context["schema"] == schema, "prompt includes the schema catalog")
    check(prompt_context["relationships"][0]["referred_table"] == "customers", "prompt includes foreign-key join paths")
    check(prompt_context["resolved_tables"] == ["products"], "prompt includes schema-linked tables")
    check(bool(prompt_context["similar_examples"]), "prompt includes retrieved examples")
    check("SELECT" in provider.messages[0]["content"], "system prompt requires read-only SQL generation")
    check(result.examples_used == ["top-products"], "response reports which examples informed the prompt")
    check(
        prompt_context["value_normalization_hints"] == {},
        "prompt omits value-normalization hints when country/region columns are absent",
    )

    normalized_question_provider = FakeProvider(
        json.dumps(
            {
                "sql": "SELECT region FROM customers WHERE country = 'US' AND region = 'North' LIMIT 10",
                "explanation": "Returns customers from the US and North region.",
            }
        )
    )
    normalized_result = generate_sql(
        question="Give me customer list from USA in noth region",
        dialect="sqlite",
        schema_catalog={"customers": ["customer_id", "full_name", "country", "region"]},
        examples=[],
        provider_factory=lambda: normalized_question_provider,
        model="test-model",
    )
    check(normalized_result.sql.startswith("SELECT"), "normalized-question generation still returns SQL")
    assert normalized_question_provider.messages is not None
    normalized_context = json.loads(normalized_question_provider.messages[1]["content"])
    check(
        normalized_context["question"] == "Give me customer list from US in north region",
        "prompt question normalizes USA and common region typo variants",
    )
    check(
        "customers.country" in normalized_context["value_normalization_hints"]
        and "customers.region" in normalized_context["value_normalization_hints"],
        "prompt includes country/region normalization hints when schema supports them",
    )

    uk_question_provider = FakeProvider(
        json.dumps(
            {
                "sql": "SELECT COUNT(*) AS customer_count FROM customers WHERE country = 'GB'",
                "explanation": "Counts customers from GB.",
            }
        )
    )
    uk_result = generate_sql(
        question="How many customer from United Kingdom",
        dialect="sqlite",
        schema_catalog={"customers": ["customer_id", "full_name", "country", "region"]},
        examples=[],
        provider_factory=lambda: uk_question_provider,
        model="test-model",
    )
    check(uk_result.sql.startswith("SELECT"), "United Kingdom normalization still yields SQL")
    assert uk_question_provider.messages is not None
    uk_context = json.loads(uk_question_provider.messages[1]["content"])
    check(
        uk_context["question"] == "How many customer from GB",
        "prompt question normalizes United Kingdom to GB",
    )

    ca_au_question_provider = FakeProvider(
        json.dumps(
            {
                "sql": "SELECT COUNT(*) AS customer_count FROM customers WHERE country IN ('CA', 'AU')",
                "explanation": "Counts customers from CA and AU.",
            }
        )
    )
    ca_au_result = generate_sql(
        question="How many customer from Canada and Australia",
        dialect="sqlite",
        schema_catalog={"customers": ["customer_id", "full_name", "country", "region"]},
        examples=[],
        provider_factory=lambda: ca_au_question_provider,
        model="test-model",
    )
    check(ca_au_result.sql.startswith("SELECT"), "Canada/Australia normalization still yields SQL")
    assert ca_au_question_provider.messages is not None
    ca_au_context = json.loads(ca_au_question_provider.messages[1]["content"])
    check(
        ca_au_context["question"] == "How many customer from CA and AU",
        "prompt question normalizes Canada and Australia to CA and AU",
    )

    never_called = FakeProvider("{}")
    provider_factory_calls: list[bool] = []
    try:
        generate_sql(
            question="Delete all orders",
            dialect="sqlite",
            schema_catalog=schema,
            examples=examples,
            provider_factory=lambda: (provider_factory_calls.append(True), never_called)[1],
            model="test-model",
        )
    except UnsupportedQuestionError:
        check(never_called.messages is None, "unsupported destructive requests are blocked before provider calls")
        check(not provider_factory_calls, "provider factory is not called for destructive requests")
    else:
        raise AssertionError("Destructive request was not rejected")

    malformed_provider = FakeProvider("not json")
    try:
        generate_sql(
            question="List product names",
            dialect="sqlite",
            schema_catalog=schema,
            examples=[],
            provider_factory=lambda: malformed_provider,
            model="test-model",
        )
    except Exception as error:
        check(error.__class__.__name__ == "InvalidGenerationResponseError", "malformed provider JSON is rejected")
    else:
        raise AssertionError("Malformed model response was accepted")

    privacy_provider = FakeProvider(
        json.dumps(
            {
                "sql": "SELECT email FROM customers LIMIT 10",
                "explanation": "Returns customer email addresses.",
            }
        )
    )
    try:
        generate_sql(
            question="List customers",
            dialect="sqlite",
            schema_catalog={"customers": ["customer_id", "full_name", "email"]},
            examples=[],
            provider_factory=lambda: privacy_provider,
            model="test-model",
        )
    except Exception as error:
        check(
            error.__class__.__name__ == "InvalidGenerationResponseError",
            "unrequested customer email projection is rejected",
        )
    else:
        raise AssertionError("Unrequested customer email projection was accepted")


async def validate_generation_api() -> None:
    fake_provider = FakeProvider(
        json.dumps(
            {
                "sql": "SELECT product_name FROM products LIMIT 10",
                "explanation": "Lists product names.",
            }
        )
    )
    app.dependency_overrides[get_sql_completion_provider] = lambda: lambda: fake_provider
    app.dependency_overrides[get_safety_guardrail] = lambda: lambda: FakeSafetyGuardrail()
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post("/query/generate", json={"question": "List product names"})
            check(response.status_code == 200, "generation API returns success with an injected fake provider")
            body = response.json()
            check(body["sql"] == "SELECT product_name FROM products LIMIT 10", "API returns generated SQL")
            check(body["model"] == get_groq_model(), "API uses the model configured in the environment")
            check(fake_provider.messages is not None, "API passed generation context to the provider")
            assert fake_provider.messages is not None
            api_context = json.loads(fake_provider.messages[1]["content"])
            check(bool(api_context["relationships"]), "API sends configured database foreign keys to the model")

            unsupported_response = await client.post(
                "/query/generate",
                json={"question": "Delete all orders"},
            )
            check(unsupported_response.status_code == 422, "API rejects destructive intent before generation")

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
                ambiguous_factory_calls: list[bool] = []
                app.dependency_overrides[get_sql_completion_provider] = lambda: (
                    lambda: (ambiguous_factory_calls.append(True), fake_provider)[1]
                )
                ambiguous_response = await client.post(
                    "/query/generate",
                    json={"question": "Show amount"},
                )
                check(ambiguous_response.status_code == 409, "API asks for clarification on ambiguous schema terms")
                check(
                    len(ambiguous_response.json()["detail"]["ambiguities"][0]["candidates"]) >= 2,
                    "ambiguity response includes candidate columns",
                )
                check(not ambiguous_factory_calls, "provider factory is not called for ambiguous requests")
            finally:
                app.dependency_overrides.pop(get_database_engine, None)
                ambiguous_engine.dispose()

            unsafe_provider = FakeProvider(
                json.dumps(
                    {
                        "sql": "SELECT customer_id, full_name, email FROM customers LIMIT 10",
                        "explanation": "Returns customer details.",
                    }
                )
            )
            app.dependency_overrides[get_sql_completion_provider] = lambda: lambda: unsafe_provider
            unsafe_response = await client.post(
                "/query/generate",
                json={"question": "List product names"},
            )
            check(unsafe_response.status_code == 422, "API rejects unrelated generated SQL before returning it")
            check(
                "customer_id" not in unsafe_response.text,
                "rejected SQL is not exposed in the API error response",
            )
            check(
                "unrelated to your question" in unsafe_response.json()["detail"].lower(),
                "API returns an actionable SQL-validation message",
            )
            off_scope_provider = FakeProvider("{}")
            off_scope_factory_calls: list[bool] = []
            app.dependency_overrides[get_sql_completion_provider] = lambda: (
                lambda: (off_scope_factory_calls.append(True), off_scope_provider)[1]
            )

            off_scope_response = await client.post(
                "/query/generate",
                json={"question": "give me python code to write READ only SQL"},
            )
            check(off_scope_response.status_code == 422, "API rejects the off-scope Python-code request")
            check(
                off_scope_response.json()["detail"] == "Request could not be completed safely."
                and "programming" not in off_scope_response.text,
                "off-scope rejection uses a generic message without internal details",
            )
            check(off_scope_provider.messages is None, "off-scope input is rejected before the provider is called")
            check(not off_scope_factory_calls, "Groq provider factory is not constructed for off-scope input")
    finally:
        app.dependency_overrides.pop(get_sql_completion_provider, None)
        app.dependency_overrides.pop(get_safety_guardrail, None)

    with patch.dict(os.environ, {"GROQ_API_KEY": ""}):
        missing_key_transport = httpx.ASGITransport(app=app)
        app.dependency_overrides[get_safety_guardrail] = lambda: lambda: FakeSafetyGuardrail()
        async with httpx.AsyncClient(transport=missing_key_transport, base_url="http://testserver") as client:
            missing_key_response = await client.post(
                "/query/generate",
                json={"question": "List product names"},
            )
            app.dependency_overrides.pop(get_safety_guardrail, None)
    check(missing_key_response.status_code == 503, "missing Groq API key produces a clear service-unavailable response")
    check(
        "GROQ_API_KEY" in missing_key_response.json()["detail"],
        "missing-key response tells the user which local setting is required",
    )


def main() -> None:
    validate_generator_service()
    asyncio.run(validate_generation_api())
    print("SQL generator validation passed.")


if __name__ == "__main__":
    main()