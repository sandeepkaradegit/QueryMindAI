from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"
DEFAULT_GROQ_GUARDRAIL_MODEL = "openai/gpt-oss-120b"
DEFAULT_MAX_ROWS = 100
MAX_ALLOWED_ROWS = 1000
DEFAULT_QUERY_TIMEOUT_SECONDS = 5.0
MAX_QUERY_TIMEOUT_SECONDS = 30.0
DEFAULT_SESSION_TTL_SECONDS = 12 * 60 * 60
DEFAULT_FRONTEND_ORIGIN = "http://localhost:3000"
DEFAULT_QUESTION_NORMALIZATION_ALIASES: dict[str, str] = {
    "usa": "US",
    "united states": "US",
    "united states of america": "US",
    "uk": "GB",
    "united kingdom": "GB",
    "canada": "CA",
    "australia": "AU",
    "noth": "north",
}
DEFAULT_VALUE_NORMALIZATION_HINTS: dict[str, dict[str, dict[str, str]]] = {
    "customers.country": {
        "aliases": {
            "australia": "AU",
            "britain": "GB",
            "canada": "CA",
            "great britain": "GB",
            "uk": "GB",
            "united kingdom": "GB",
            "united states": "US",
            "united states of america": "US",
            "usa": "US",
        }
    },
    "customers.region": {"aliases": {"noth": "North"}},
}

load_dotenv(PROJECT_ROOT / ".env")


def get_groq_api_key() -> str | None:
    api_key = os.environ.get("GROQ_API_KEY", "").strip()
    return api_key or None


def get_groq_model() -> str:
    return os.environ.get("GROQ_MODEL", DEFAULT_GROQ_MODEL).strip() or DEFAULT_GROQ_MODEL


def get_groq_guardrail_model() -> str:
    return (
        os.environ.get("GROQ_GUARDRAIL_MODEL", DEFAULT_GROQ_GUARDRAIL_MODEL).strip()
        or DEFAULT_GROQ_GUARDRAIL_MODEL
    )


def get_max_rows() -> int:
    try:
        configured_rows = int(os.environ.get("QUERYMIND_MAX_ROWS", DEFAULT_MAX_ROWS))
    except ValueError:
        return DEFAULT_MAX_ROWS
    return min(max(configured_rows, 1), MAX_ALLOWED_ROWS)


def get_query_timeout_seconds() -> float:
    try:
        configured_timeout = float(
            os.environ.get("QUERYMIND_QUERY_TIMEOUT_SECONDS", DEFAULT_QUERY_TIMEOUT_SECONDS)
        )
    except ValueError:
        return DEFAULT_QUERY_TIMEOUT_SECONDS
    return min(max(configured_timeout, 0.1), MAX_QUERY_TIMEOUT_SECONDS)


def get_session_ttl_seconds() -> int:
    try:
        return int(os.environ.get("QUERYMIND_SESSION_TTL_SECONDS", DEFAULT_SESSION_TTL_SECONDS))
    except ValueError:
        return DEFAULT_SESSION_TTL_SECONDS


def get_frontend_origin() -> str:
    return os.environ.get("QUERYMIND_FRONTEND_ORIGIN", DEFAULT_FRONTEND_ORIGIN).strip() or DEFAULT_FRONTEND_ORIGIN


def get_default_question_normalization_aliases() -> dict[str, str]:
    return dict(DEFAULT_QUESTION_NORMALIZATION_ALIASES)


def get_default_value_normalization_hints() -> dict[str, dict[str, dict[str, str]]]:
    return {
        key: {"aliases": dict(value["aliases"])}
        for key, value in DEFAULT_VALUE_NORMALIZATION_HINTS.items()
    }