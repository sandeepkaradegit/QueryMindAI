from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

_fd, _path = tempfile.mkstemp(suffix=".sqlite3")
os.close(_fd)
os.environ["QUERYMIND_ADMIN_DB_PATH"] = _path
os.environ["QUERYMIND_EXAMPLE_RETRIEVAL"] = "lexical"
for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
    os.environ.pop(name, None)

import httpx

from app.api.routes import get_safety_guardrail, get_sql_completion_provider
from app.main import app
from app.services import llm_providers
from app.services.openevals_guardrails import GuardrailVerdict
from app.services.sql_generator import ProviderUnavailableError
from validation_helpers import check



class FakeProvider:
    def complete(self, messages, model):
        return '{"sql": "SELECT product_name FROM products LIMIT 5", "explanation": "Lists products."}'


class FakeGuardrail:
    def evaluate_input(self, question, schema_catalog):
        return GuardrailVerdict(verdict="allow", reason_code="none")

    def evaluate_output(self, question, sql, explanation, schema_catalog):
        return GuardrailVerdict(verdict="allow", reason_code="none")


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def validate_providers() -> None:
    for provider in ("openai", "anthropic"):
        try:
            llm_providers.build_provider(provider, temperature=0.0, max_tokens=None)
        except llm_providers.ProviderNotConfiguredError:
            check(True, f"{provider} without an API key raises a configuration error")
        else:
            raise AssertionError(f"{provider} should require an API key")

    calls: list[dict] = []
    original_post = httpx.post

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append({"url": url, "json": json, "headers": headers})
        if "anthropic" in url:
            return FakeResponse(
                {"content": [{"type": "text", "text": "{}"}], "usage": {"input_tokens": 3, "output_tokens": 2}}
            )
        return FakeResponse(
            {
                "choices": [{"message": {"content": "{}"}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
            }
        )

    llm_providers.httpx.post = fake_post
    try:
        os.environ["OPENAI_API_KEY"] = "test-key"
        os.environ["ANTHROPIC_API_KEY"] = "test-key"
        messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]

        openai = llm_providers.build_provider("openai", temperature=0.3, max_tokens=50)
        check(openai.complete(messages, "gpt-4o-mini") == "{}", "openai provider returns completion text")
        check(calls[-1]["json"]["max_tokens"] == 50 and calls[-1]["json"]["temperature"] == 0.3, "openai honours temperature and max tokens")
        check(openai.last_usage["total_tokens"] == 5, "openai reports token usage")

        anthropic = llm_providers.build_provider("anthropic", temperature=0.3, max_tokens=50)
        anthropic.complete(messages, "claude")
        body = calls[-1]["json"]
        check(body["system"] == "sys" and body["messages"] == [{"role": "user", "content": "hi"}], "anthropic splits the system prompt")
        check(anthropic.last_usage["total_tokens"] == 5, "anthropic reports token usage")

        ollama = llm_providers.build_provider("ollama", temperature=0.0, max_tokens=None)
        ollama.complete(messages, "llama3.1")
        check("11434/v1/chat/completions" in calls[-1]["url"], "ollama targets the local OpenAI-compatible endpoint")
    finally:
        llm_providers.httpx.post = original_post
        os.environ.pop("OPENAI_API_KEY", None)
        os.environ.pop("ANTHROPIC_API_KEY", None)

    def failing_post(*args, **kwargs):
        raise httpx.ConnectError("down")

    llm_providers.httpx.post = failing_post
    try:
        llm_providers.build_provider("ollama", temperature=0.0, max_tokens=None).complete([], "m")
    except ProviderUnavailableError:
        check(True, "unreachable providers raise ProviderUnavailableError")
    finally:
        llm_providers.httpx.post = original_post


async def validate_api() -> None:
    app.dependency_overrides[get_sql_completion_provider] = lambda: lambda: FakeProvider()
    app.dependency_overrides[get_safety_guardrail] = lambda: lambda: FakeGuardrail()
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            await client.post("/auth/bootstrap", json={"username": "admin", "password": "correct-horse-battery"})
            await client.post("/auth/login", json={"username": "admin", "password": "correct-horse-battery"})

            initial = await client.get("/admin/config")
            check(initial.status_code == 200 and initial.json()["provider"] == "groq", "/admin/config alias returns the config")
            check(set(initial.json()["default_models"]) == {"groq", "openai", "anthropic", "ollama"}, "config lists default models per provider")

            bad = await client.put("/admin/config", json={"provider": "bogus"})
            check(bad.status_code == 422, "unknown providers are rejected")

            saved = await client.put(
                "/admin/config",
                json={"provider": "ollama", "temperature": 0.2, "max_tokens": 300, "transpile_dialect": "postgresql"},
            )
            body = saved.json()
            check(saved.status_code == 200 and body["provider"] == "ollama" and body["max_tokens"] == 300, "provider and max tokens can be saved")
            check(body["transpile_dialect"] == "postgresql", "transpile dialect is saved")

            via_old_path = await client.get("/admin/model-config")
            check(via_old_path.json()["provider"] == "ollama", "/admin/model-config returns the same config")

            generated = await client.post("/query/generate", json={"question": "List product names"})
            check(generated.status_code == 200, "generation works with the selected provider")
            check(bool(generated.json()["transpiled_sql"]), "transpiled SQL is returned when a transpile dialect is set")

            await client.put("/admin/config", json={"provider": "groq", "transpile_dialect": None})
            plain = await client.post("/query/generate", json={"question": "List product names"})
            check(plain.json()["transpiled_sql"] is None, "no transpiled SQL when the toggle is off")
    finally:
        app.dependency_overrides.clear()


if __name__ == "__main__":
    validate_providers()
    asyncio.run(validate_api())
    print("Model config validation passed.")
