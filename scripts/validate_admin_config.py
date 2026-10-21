from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import httpx
from sqlalchemy import inspect


ROOT = Path(__file__).resolve().parents[1]
BACKEND_PATH = ROOT / "backend"
if str(BACKEND_PATH) not in sys.path:
    sys.path.insert(0, str(BACKEND_PATH))

# Isolate admin metadata storage from the real data/querymind_admin.sqlite3 before
# any module reads it, so this validator never mutates the developer's local state.
_admin_db_fd, _admin_db_path_str = tempfile.mkstemp(suffix=".sqlite3")
os.close(_admin_db_fd)
_TEMP_ADMIN_DB = Path(_admin_db_path_str)
os.environ["QUERYMIND_ADMIN_DB_PATH"] = str(_TEMP_ADMIN_DB)

from app import admin_store
from app.api import routes as routes_module
from app.database import create_database_engine, get_database_engine
from app.main import app
from app.services.openevals_guardrails import GuardrailVerdict
from app.auth import get_current_user
from validation_helpers import check

app.dependency_overrides[get_current_user] = lambda: admin_store.AdminUserRecord(
    id=1, username="test-admin", password_hash="", password_salt="", role="admin", created_at=""
)


def _new_temp_path(suffix: str) -> Path:
    file_descriptor, path = tempfile.mkstemp(suffix=suffix)
    os.close(file_descriptor)
    return Path(path)



class FakeProvider:
    def __init__(self, response: str) -> None:
        self.response = response
        self.model: str | None = None
        self.messages: list[dict[str, str]] | None = None

    def complete(self, messages: list[dict[str, str]], model: str) -> str:
        self.model = model
        self.messages = messages
        return self.response


class FakeSafetyGuardrail:
    def evaluate_input(self, question, schema_catalog):
        return GuardrailVerdict(verdict="allow", reason_code="none")

    def evaluate_output(self, question, sql, explanation, schema_catalog):
        return GuardrailVerdict(verdict="allow", reason_code="none")


def validate_database_connection_crud() -> None:
    check(admin_store.list_database_connections() == [], "admin store starts with no configured connections")
    check(admin_store.get_active_database_connection() is None, "no connection is active by default")

    temp_db_path = _new_temp_path(".sqlite3")
    record = admin_store.create_database_connection(
        name="temp-test-db",
        dialect="sqlite",
        connection_url=f"sqlite:///{temp_db_path.as_posix()}",
    )
    check(record.is_active is False, "newly created connections are inactive by default")

    activated = admin_store.activate_database_connection(record.id)
    check(activated is not None and activated.is_active, "activating a connection marks it active")
    check(admin_store.get_active_database_connection().id == record.id, "the active connection is retrievable")

    deleted = admin_store.delete_database_connection(record.id)
    check(deleted, "a configured connection can be deleted")
    check(admin_store.get_active_database_connection() is None, "deleting the active connection clears it")
    temp_db_path.unlink(missing_ok=True)


def validate_get_database_engine_uses_active_connection() -> None:
    temp_db_path = _new_temp_path(".sqlite3")
    marker_engine = create_database_engine(f"sqlite:///{temp_db_path.as_posix()}")
    with marker_engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE admin_marker (id INTEGER PRIMARY KEY)")
    marker_engine.dispose()

    record = admin_store.create_database_connection(
        name="active-marker-db",
        dialect="sqlite",
        connection_url=f"sqlite:///{temp_db_path.as_posix()}",
    )
    admin_store.activate_database_connection(record.id)
    try:
        engine_generator = get_database_engine()
        engine = next(engine_generator)
        try:
            tables = inspect(engine).get_table_names()
            check("admin_marker" in tables, "get_database_engine connects to the admin-activated connection")
        finally:
            try:
                next(engine_generator)
            except StopIteration:
                pass
    finally:
        admin_store.delete_database_connection(record.id)
        temp_db_path.unlink(missing_ok=True)


async def validate_admin_api() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        mismatched = await client.post(
            "/admin/database",
            json={"name": "mismatched", "dialect": "postgresql", "connection_url": "sqlite:///:memory:"},
        )
        check(mismatched.status_code == 422, "a connection URL backend must match the declared dialect")

        unreachable = await client.post(
            "/admin/database",
            json={
                "name": "unreachable",
                "dialect": "sqlite",
                "connection_url": "sqlite:////definitely/not/a/real/directory/db.sqlite3",
            },
        )
        check(unreachable.status_code == 422, "connections that cannot be established are rejected")

        temp_db_path = _new_temp_path(".sqlite3")
        created = await client.post(
            "/admin/database",
            json={
                "name": "api-created-db",
                "dialect": "sqlite",
                "connection_url": f"sqlite:///{temp_db_path.as_posix()}",
            },
        )
        check(created.status_code == 201, "a valid, reachable connection can be registered")
        connection_id = created.json()["id"]

        listed = await client.get("/admin/database")
        check(
            any(entry["id"] == connection_id for entry in listed.json()),
            "registered connections are listed",
        )

        activated = await client.post(f"/admin/database/{connection_id}/activate")
        check(activated.status_code == 200 and activated.json()["is_active"], "a connection can be activated")

        deleted = await client.delete(f"/admin/database/{connection_id}")
        check(deleted.status_code == 204, "a connection can be deleted")
        missing = await client.delete(f"/admin/database/{connection_id}")
        check(missing.status_code == 404, "deleting an unknown connection reports 404")
        temp_db_path.unlink(missing_ok=True)

        annotation = await client.put(
            "/admin/schema/annotations",
            json={
                "table_name": "customers",
                "column_name": "email",
                "description": "Customer email address",
                "synonyms": ["e-mail"],
                "is_sensitive": True,
            },
        )
        check(annotation.status_code == 200 and annotation.json()["is_sensitive"], "sensitive column flags persist")
        annotations = await client.get("/admin/schema/annotations")
        check(
            any(item["column_name"] == "email" and item["is_sensitive"] for item in annotations.json()),
            "schema annotations are listed",
        )

        rejected_provider = await client.put(
            "/admin/model-config",
            json={"provider": "openai", "temperature": 0.2},
        )
        check(rejected_provider.status_code == 422, "unsupported model providers are rejected")

        updated_config = await client.put(
            "/admin/model-config",
            json={
                "provider": "groq",
                "model": "custom-generation-model",
                "guardrail_model": "custom-guardrail-model",
                "temperature": 0.3,
                "max_tokens": 256,
            },
        )
        check(updated_config.status_code == 200, "model configuration can be updated")
        fetched_config = await client.get("/admin/model-config")
        check(
            fetched_config.json()["model"] == "custom-generation-model"
            and fetched_config.json()["max_tokens"] == 256,
            "updated model configuration is persisted and retrievable",
        )


async def validate_sensitive_column_excluded_from_generation() -> None:
    admin_store.upsert_schema_annotation(
        table_name="customers",
        column_name="email",
        description="Customer email address",
        synonyms=[],
        is_sensitive=True,
    )
    fake_provider = FakeProvider(
        json.dumps(
            {
                "sql": "SELECT full_name FROM customers LIMIT 10",
                "explanation": "Lists customer names.",
            }
        )
    )
    app.dependency_overrides[routes_module.get_sql_completion_provider] = lambda: lambda: fake_provider
    app.dependency_overrides[routes_module.get_safety_guardrail] = lambda: lambda: FakeSafetyGuardrail()
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post("/query/generate", json={"question": "List customer full names"})
            check(response.status_code == 200, "generation succeeds with a sensitive column configured")
            assert fake_provider.messages is not None
            prompt_schema = json.loads(fake_provider.messages[1]["content"])["schema"]
            check(
                "email" not in prompt_schema.get("customers", []),
                "sensitive columns are excluded from the prompt schema sent to the model",
            )
            check(
                "full_name" in prompt_schema.get("customers", []),
                "non-sensitive columns remain visible to the model",
            )
    finally:
        app.dependency_overrides.pop(routes_module.get_sql_completion_provider, None)
        app.dependency_overrides.pop(routes_module.get_safety_guardrail, None)


async def validate_model_config_used_in_generation() -> None:
    admin_store.update_model_config(
        provider="groq",
        model="admin-configured-model",
        guardrail_model="admin-configured-guardrail",
        temperature=0.5,
        max_tokens=128,
    )
    fake_provider = FakeProvider(
        json.dumps({"sql": "SELECT product_name FROM products LIMIT 10", "explanation": "Lists product names."})
    )
    app.dependency_overrides[routes_module.get_sql_completion_provider] = lambda: lambda: fake_provider
    app.dependency_overrides[routes_module.get_safety_guardrail] = lambda: lambda: FakeSafetyGuardrail()
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post("/query/generate", json={"question": "List product names"})
            check(response.status_code == 200, "generation succeeds with an admin-configured model")
            check(
                fake_provider.model == "admin-configured-model",
                "the admin-configured model overrides the environment default",
            )
    finally:
        app.dependency_overrides.pop(routes_module.get_sql_completion_provider, None)
        app.dependency_overrides.pop(routes_module.get_safety_guardrail, None)


def validate_provider_factory_applies_model_config() -> None:
    admin_store.update_model_config(
        provider="groq",
        model="factory-model",
        guardrail_model="factory-guardrail",
        temperature=0.7,
        max_tokens=512,
    )
    captured: dict[str, object] = {}

    class FakeGroqClient:
        def __init__(self, api_key: str) -> None:
            captured["api_key"] = api_key

    with patch.dict(os.environ, {"GROQ_API_KEY": "test-groq-key"}), patch(
        "app.services.sql_generator.Groq", FakeGroqClient
    ):
        provider = routes_module.get_sql_completion_provider()()
        check(provider._temperature == 0.7, "the completion provider uses the admin-configured temperature")
        check(provider._max_tokens == 512, "the completion provider uses the admin-configured token budget")

    captured_guardrail: dict[str, object] = {}

    class FakeGuardrailClient:
        def __init__(self, *, api_key: str, model: str) -> None:
            captured_guardrail["model"] = model

    with patch.dict(os.environ, {"GROQ_API_KEY": "test-groq-key"}), patch(
        "app.api.routes.OpenEvalsGroqGuardrail", FakeGuardrailClient
    ):
        routes_module.get_safety_guardrail()()
        check(
            captured_guardrail["model"] == "factory-guardrail",
            "the guardrail factory uses the admin-configured guardrail model",
        )


def main() -> None:
    try:
        validate_database_connection_crud()
        validate_get_database_engine_uses_active_connection()
        asyncio.run(validate_admin_api())
        asyncio.run(validate_sensitive_column_excluded_from_generation())
        asyncio.run(validate_model_config_used_in_generation())
        validate_provider_factory_applies_model_config()
        print("Admin configuration validation passed.")
    finally:
        _TEMP_ADMIN_DB.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
