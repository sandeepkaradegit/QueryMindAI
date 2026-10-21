from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.classification import QueryType


class HealthResponse(BaseModel):
    status: Literal["ok"]


class ConversationHistoryTurn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    sql: str = Field(min_length=1, max_length=20000)
    nl_summary: str | None = Field(default=None, max_length=1000)

    @field_validator("question", "sql")
    @classmethod
    def reject_blank_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("conversation history fields must contain non-whitespace text")
        return normalized

    @field_validator("nl_summary")
    @classmethod
    def normalize_optional_summary(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    conversation_history: list[ConversationHistoryTurn] = Field(default_factory=list, max_length=12)
    include_explanation: bool = True

    @field_validator("question")
    @classmethod
    def reject_blank_question(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("question must contain non-whitespace text")
        return normalized


class QueryResponse(BaseModel):
    status: Literal["ok"]
    question: str
    sql: str
    explanation: str | None
    query_type: QueryType
    examples_used: list[str]
    columns: list[str]
    rows: list[dict[str, object]]
    row_count: int
    latency_ms: float
    nl_summary: str
    transpiled_sql: str | None = None
    transpile_dialect: str | None = None
    cache_hit: bool = False
    cache_similarity: float | None = None