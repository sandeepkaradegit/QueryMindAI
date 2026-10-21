"""LLM provider adapters selectable from Model Config (Groq, OpenAI, Anthropic, Ollama)."""

from __future__ import annotations

import os
from typing import Literal

import httpx

from app.config import get_groq_api_key, get_groq_model
from app.services.sql_generator import CompletionProvider, GroqCompletionProvider, ProviderUnavailableError

Provider = Literal["groq", "openai", "anthropic", "ollama"]
PROVIDERS: tuple[str, ...] = ("groq", "openai", "anthropic", "ollama")

DEFAULT_MODELS = {
    "openai": "gpt-4o-mini",
    "anthropic": "claude-sonnet-4-20250514",
    "ollama": "llama3.1",
}
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
REQUEST_TIMEOUT_SECONDS = 60.0


class ProviderNotConfiguredError(RuntimeError):
    """Raised when the selected provider lacks the credentials or settings it needs."""


def _env(name: str) -> str | None:
    return os.environ.get(name, "").strip() or None


def default_model(provider: str) -> str:
    return get_groq_model() if provider == "groq" else DEFAULT_MODELS[provider]


class OpenAICompatibleProvider:
    """Chat-completions client for OpenAI and for Ollama's OpenAI-compatible endpoint."""

    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        api_key: str | None,
        temperature: float,
        max_tokens: int | None,
    ) -> None:
        self._name = name
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._temperature = temperature
        self._max_tokens = max_tokens
        self.last_usage: dict[str, int | None] | None = None

    def complete(self, messages: list[dict[str, str]], model: str) -> str:
        body: dict[str, object] = {
            "model": model,
            "messages": messages,
            "temperature": self._temperature,
            "response_format": {"type": "json_object"},
        }
        if self._max_tokens is not None:
            body["max_tokens"] = self._max_tokens
        try:
            response = httpx.post(self._url, json=body, headers=self._headers, timeout=REQUEST_TIMEOUT_SECONDS)
            response.raise_for_status()
            payload = response.json()
            content = payload["choices"][0]["message"]["content"]
            usage = payload.get("usage") or {}
        except Exception:
            raise ProviderUnavailableError(f"The {self._name} request could not be completed.") from None
        self.last_usage = {
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
        } if usage else None
        if not content:
            raise ProviderUnavailableError(f"The {self._name} response did not contain content.")
        return content


class AnthropicProvider:
    def __init__(self, *, api_key: str, temperature: float, max_tokens: int | None) -> None:
        self._headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
        self._temperature = min(temperature, 1.0)
        self._max_tokens = max_tokens or 1024
        self.last_usage: dict[str, int | None] | None = None

    def complete(self, messages: list[dict[str, str]], model: str) -> str:
        system = "\n\n".join(message["content"] for message in messages if message["role"] == "system")
        conversation = [message for message in messages if message["role"] != "system"]
        body: dict[str, object] = {
            "model": model,
            "max_tokens": self._max_tokens,
            "temperature": self._temperature,
            "messages": conversation,
        }
        if system:
            body["system"] = system
        try:
            response = httpx.post(ANTHROPIC_URL, json=body, headers=self._headers, timeout=REQUEST_TIMEOUT_SECONDS)
            response.raise_for_status()
            payload = response.json()
            content = "".join(block.get("text", "") for block in payload["content"] if block.get("type") == "text")
            usage = payload.get("usage") or {}
        except Exception:
            raise ProviderUnavailableError("The Anthropic request could not be completed.") from None
        prompt_tokens = usage.get("input_tokens")
        completion_tokens = usage.get("output_tokens")
        self.last_usage = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": (prompt_tokens or 0) + (completion_tokens or 0) if usage else None,
        } if usage else None
        if not content:
            raise ProviderUnavailableError("The Anthropic response did not contain content.")
        return content


def build_provider(provider: str, *, temperature: float, max_tokens: int | None) -> CompletionProvider:
    if provider == "groq":
        api_key = get_groq_api_key()
        if api_key is None:
            raise ProviderNotConfiguredError("Groq is not configured. Set GROQ_API_KEY in the local .env file.")
        return GroqCompletionProvider(api_key, temperature=temperature, max_tokens=max_tokens)
    if provider == "openai":
        api_key = _env("OPENAI_API_KEY")
        if api_key is None:
            raise ProviderNotConfiguredError("OpenAI is not configured. Set OPENAI_API_KEY in the local .env file.")
        return OpenAICompatibleProvider(
            name="OpenAI",
            base_url=_env("OPENAI_BASE_URL") or DEFAULT_OPENAI_BASE_URL,
            api_key=api_key,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    if provider == "anthropic":
        api_key = _env("ANTHROPIC_API_KEY")
        if api_key is None:
            raise ProviderNotConfiguredError(
                "Anthropic is not configured. Set ANTHROPIC_API_KEY in the local .env file."
            )
        return AnthropicProvider(api_key=api_key, temperature=temperature, max_tokens=max_tokens)
    if provider == "ollama":
        return OpenAICompatibleProvider(
            name="Ollama",
            base_url=f"{(_env('OLLAMA_BASE_URL') or DEFAULT_OLLAMA_BASE_URL).rstrip('/')}/v1",
            api_key=None,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    raise ProviderNotConfiguredError(f"Unsupported provider: {provider}")
