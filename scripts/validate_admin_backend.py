from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND_PATH = ROOT / "backend"
if str(BACKEND_PATH) not in sys.path:
    sys.path.insert(0, str(BACKEND_PATH))

# Isolate admin metadata storage (users, sessions, examples, logs, guardrails) from the
# real data/querymind_admin.sqlite3 before any module reads it.
_admin_db_fd, _admin_db_path_str = tempfile.mkstemp(suffix=".sqlite3")
os.close(_admin_db_fd)
_TEMP_ADMIN_DB = Path(_admin_db_path_str)
os.environ["QUERYMIND_ADMIN_DB_PATH"] = str(_TEMP_ADMIN_DB)

import httpx

from app.api.routes import get_safety_guardrail, get_sql_completion_provider
from app.main import app
from app.services.openevals_guardrails import GuardrailVerdict
from validation_helpers import check



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


async def validate_auth_flow() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        unauthenticated = await client.get("/admin/examples")
        check(unauthenticated.status_code == 401, "protected routes reject requests without a session")

        bootstrap = await client.post(
            "/auth/bootstrap", json={"username": "admin", "password": "correct-horse-battery"}
        )
        check(bootstrap.status_code == 201, "the first admin user can bootstrap an account")
        check("qm_session" in client.cookies, "bootstrap sets a session cookie")

        second_bootstrap = await client.post(
            "/auth/bootstrap", json={"username": "someone-else", "password": "another-password"}
        )
        check(second_bootstrap.status_code == 409, "bootstrap only runs once")

        me = await client.get("/auth/me")
        check(me.status_code == 200 and me.json()["username"] == "admin", "the session identifies the logged-in user")

        authenticated = await client.get("/admin/examples")
        check(authenticated.status_code == 200, "a valid session can reach protected routes")

        await client.post("/auth/logout")
        after_logout = await client.get("/admin/examples")
        check(after_logout.status_code == 401, "logout invalidates the session")

        bad_login = await client.post("/auth/login", json={"username": "admin", "password": "wrong-password"})
        check(bad_login.status_code == 401, "an incorrect password is rejected")

        good_login = await client.post("/auth/login", json={"username": "admin", "password": "correct-horse-battery"})
        check(good_login.status_code == 200, "a correct password logs the user back in")


async def validate_examples_crud() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        await client.post("/auth/login", json={"username": "admin", "password": "correct-horse-battery"})

        seeded = await client.get("/admin/examples")
        check(seeded.status_code == 200 and len(seeded.json()) >= 30, "the starter example library is seeded once")

        created = await client.post(
            "/admin/examples",
            json={
                "id": "validator-example",
                "question": "List all shipment carriers",
                "sql": "SELECT DISTINCT carrier FROM shipments",
                "query_type": "SELECT_SIMPLE",
            },
        )
        check(created.status_code == 201, "a new example can be created")

        duplicate = await client.post(
            "/admin/examples",
            json={
                "id": "validator-example",
                "question": "Duplicate",
                "sql": "SELECT 1",
                "query_type": "SELECT_SIMPLE",
            },
        )
        check(duplicate.status_code == 409, "duplicate example IDs are rejected")

        searched = await client.get("/admin/examples", params={"search": "shipment carriers"})
        check(
            any(example["id"] == "validator-example" for example in searched.json()),
            "examples can be searched by question/SQL text",
        )

        updated = await client.put(
            "/admin/examples/validator-example",
            json={
                "question": "List all distinct shipment carriers",
                "sql": "SELECT DISTINCT carrier FROM shipments",
                "query_type": "SELECT_SIMPLE",
            },
        )
        check(updated.status_code == 200 and "distinct" in updated.json()["question"], "an example can be updated")

        deleted = await client.delete("/admin/examples/validator-example")
        check(deleted.status_code == 204, "an example can be deleted")
        missing_update = await client.put(
            "/admin/examples/validator-example",
            json={"question": "x", "sql": "SELECT 1", "query_type": "SELECT_SIMPLE"},
        )
        check(missing_update.status_code == 404, "updating a deleted example reports 404")


async def validate_query_logging() -> None:
    fake_provider = FakeProvider(
        json.dumps({"sql": "SELECT product_name FROM products LIMIT 10", "explanation": "Lists product names."})
    )
    app.dependency_overrides[get_sql_completion_provider] = lambda: lambda: fake_provider
    app.dependency_overrides[get_safety_guardrail] = lambda: lambda: FakeSafetyGuardrail()
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            await client.post("/auth/login", json={"username": "admin", "password": "correct-horse-battery"})

            await client.post("/query/generate", json={"question": "List product names"})
            await client.post("/query/generate", json={"question": "Delete all orders"})

            logs = await client.get("/admin/logs")
            check(logs.status_code == 200, "query logs can be listed")
            statuses = {entry["status"] for entry in logs.json()}
            check("generated" in statuses, "a successful generation is logged")
            check("refused" in statuses, "a refused request is logged with its outcome")

            refused_entry = next(entry for entry in logs.json() if entry["status"] == "refused")
            check(bool(refused_entry["error_reason"]), "refused log entries include a reason")

            filtered = await client.get("/admin/logs", params={"status_filter": "generated"})
            check(
                all(entry["status"] == "generated" for entry in filtered.json()),
                "query logs can be filtered by status",
            )
    finally:
        app.dependency_overrides.pop(get_sql_completion_provider, None)
        app.dependency_overrides.pop(get_safety_guardrail, None)


async def validate_guardrails_config() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        await client.post("/auth/login", json={"username": "admin", "password": "correct-horse-battery"})

        default_config = await client.get("/admin/guardrails")
        check(default_config.status_code == 200, "guardrails config can be read")

        rejected = await client.put(
            "/admin/guardrails",
            json={"max_rows": 100, "allow_write_operations": True, "allowed_table_prefixes": []},
        )
        check(rejected.status_code == 422, "enabling destructive operations is refused")

        baseline = await client.post("/query/classify", json={"question": "Show products"})
        check("products" in baseline.json()["tables_mentioned"], "products table is visible before scoping")

        scoped = await client.put(
            "/admin/guardrails",
            json={"max_rows": 50, "allow_write_operations": False, "allowed_table_prefixes": ["customers"]},
        )
        check(scoped.status_code == 200 and scoped.json()["max_rows"] == 50, "guardrails config can be updated")

        after_scope = await client.post("/query/classify", json={"question": "Show products"})
        check(
            "products" not in after_scope.json()["tables_mentioned"],
            "table-prefix scoping hides tables outside the allowed prefixes",
        )

        # Reset scope so later validators in this process are unaffected.
        await client.put(
            "/admin/guardrails",
            json={"max_rows": 100, "allow_write_operations": False, "allowed_table_prefixes": []},
        )


def main() -> None:
    try:
        asyncio.run(validate_auth_flow())
        asyncio.run(validate_examples_crud())
        asyncio.run(validate_query_logging())
        asyncio.run(validate_guardrails_config())
        print("Admin backend (auth, examples, logs, guardrails) validation passed.")
    finally:
        _TEMP_ADMIN_DB.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
