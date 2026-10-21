from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[1]
BACKEND_PATH = ROOT / "backend"
if str(BACKEND_PATH) not in sys.path:
    sys.path.insert(0, str(BACKEND_PATH))

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
from app.services.example_retriever import load_example_library, retrieve_examples
from validation_helpers import check



def validate_library_retrieval() -> None:
    examples = load_example_library()
    check(len(examples) >= 5, "curated example library loads with multiple entries")
    check(
        {example.query_type for example in examples}
        >= {QueryType.SELECT_AGGREGATE, QueryType.SELECT_JOIN, QueryType.SELECT_TEMPORAL},
        "library covers aggregate, join, and temporal patterns",
    )

    ranked = retrieve_examples("Show monthly sales by product", examples, top_k=3)
    check(bool(ranked), "relevant examples are retrieved")
    check(ranked[0].id == "monthly-sales-by-product", "closest monthly product-sales example ranks first")
    check(all(0.0 < example.similarity <= 1.0 for example in ranked), "retrieval scores are normalized similarities")
    check(len(ranked) <= 3, "retrieval respects the requested top-k bound")

    unrelated = retrieve_examples("Explain volcanic eruptions", examples, top_k=3)
    check(not unrelated, "unrelated questions do not receive arbitrary examples")
    check(not retrieve_examples("monthly sales", [], top_k=3), "empty libraries return an empty result")

    small_library = [
        QuestionSqlExample(
            id="a",
            question="List order dates",
            sql="SELECT order_date FROM orders",
            query_type=QueryType.SELECT_SIMPLE,
        ),
        QuestionSqlExample(
            id="b",
            question="Count customers by country",
            sql="SELECT country, COUNT(*) FROM customers GROUP BY country",
            query_type=QueryType.SELECT_AGGREGATE,
        ),
    ]
    check(
        retrieve_examples("orders by date", small_library, top_k=5)[0].id == "a",
        "date and order terms rank the matching example over a distractor",
    )


async def validate_retrieval_api() -> None:
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/query/examples/retrieve",
                json={"question": "Show monthly sales by product", "top_k": 2},
            )
            check(response.status_code == 200, "example retrieval endpoint responds successfully")
            payload = response.json()
            check(len(payload["examples"]) <= 2, "API response respects requested top-k")
            check(
                payload["examples"][0]["id"] == "monthly-sales-by-product",
                "API returns the most relevant example first",
            )
            check(
                "sql" in payload["examples"][0],
                "retrieved SQL is returned as prompt context without being executed",
            )

            invalid_top_k = await client.post(
                "/query/examples/retrieve",
                json={"question": "Find sales", "top_k": 6},
            )
            check(invalid_top_k.status_code == 422, "API rejects top-k values above the configured maximum")
    finally:
        pass


def main() -> None:
    validate_library_retrieval()
    asyncio.run(validate_retrieval_api())
    print("Example retrieval validation passed.")


if __name__ == "__main__":
    main()