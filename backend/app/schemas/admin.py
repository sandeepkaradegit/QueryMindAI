from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.classification import QueryType


class DatabaseConnectionCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    dialect: Literal["sqlite", "postgresql", "mysql"]
    connection_url: str = Field(min_length=1)


class DatabaseConnectionResponse(BaseModel):
    id: int
    name: str
    dialect: str
    connection_url: str
    is_active: bool
    created_at: str
    updated_at: str


class SchemaAnnotationUpsertRequest(BaseModel):
    table_name: str = Field(min_length=1)
    column_name: str | None = None
    description: str | None = None
    synonyms: list[str] = Field(default_factory=list)
    is_sensitive: bool = False


class SchemaAnnotationResponse(BaseModel):
    id: int
    table_name: str
    column_name: str | None
    description: str | None
    synonyms: list[str]
    is_sensitive: bool
    updated_at: str


class ValueNormalizationUpsertRequest(BaseModel):
    table_name: str = Field(min_length=1)
    column_name: str = Field(min_length=1)
    alias_value: str = Field(min_length=1)
    canonical_value: str = Field(min_length=1)


class ValueNormalizationResponse(BaseModel):
    id: int
    table_name: str
    column_name: str
    alias_value: str
    canonical_value: str
    updated_at: str


class ModelConfigRequest(BaseModel):
    provider: Literal["groq", "openai", "anthropic", "ollama"] = "groq"
    model: str | None = None
    guardrail_model: str | None = None
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1, le=32000)
    # Extra dialect the generated SQL is also rewritten into (via sqlglot); queries still run on the active database.
    transpile_dialect: Literal["sqlite", "postgresql", "mysql"] | None = None


class ModelConfigResponse(BaseModel):
    provider: str
    model: str | None
    guardrail_model: str | None
    temperature: float
    max_tokens: int | None
    updated_at: str
    transpile_dialect: str | None = None
    database_dialect: str | None = None
    default_models: dict[str, str] = Field(default_factory=dict)


class ExampleCreateRequest(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    question: str = Field(min_length=1)
    sql: str = Field(min_length=1)
    query_type: QueryType


class ExampleUpdateRequest(BaseModel):
    question: str = Field(min_length=1)
    sql: str = Field(min_length=1)
    query_type: QueryType


class ExampleResponse(BaseModel):
    id: str
    question: str
    sql: str
    query_type: str
    created_at: str
    updated_at: str


class QueryLogResponse(BaseModel):
    id: int
    trace_id: str | None
    question: str
    status: str
    sql: str | None
    error_reason: str | None
    row_count: int | None
    latency_ms: float | None
    created_at: str


class GuardrailsConfigRequest(BaseModel):
    max_rows: int = Field(ge=1, le=1000)
    allow_write_operations: bool = False
    allowed_table_prefixes: list[str] = Field(default_factory=list)


class GuardrailsConfigResponse(BaseModel):
    max_rows: int
    allow_write_operations: bool
    allowed_table_prefixes: list[str]
    updated_at: str


class ScriptOptionResponse(BaseModel):
    id: str
    label: str
    description: str | None = None


class ScriptDescriptorResponse(BaseModel):
    id: str
    name: str
    description: str
    category: Literal["database", "evaluation", "validation"]
    compatibility: Literal["generic", "demo_only"]
    compatibility_note: str
    new_db_flow_order: int | None = None
    new_db_flow_title: str | None = None
    new_db_flow_detail: str | None = None
    new_db_flow_option_id: str | None = None
    options: list[ScriptOptionResponse] = Field(default_factory=list)


class ScriptRunRequest(BaseModel):
    script_id: str = Field(min_length=1)
    option_id: str | None = None


class ScriptRunResponse(BaseModel):
    script_id: str
    option_id: str | None = None
    command: list[str]
    succeeded: bool
    exit_code: int
    duration_ms: int
    stdout: str
    stderr: str
    stdout_truncated: bool = False
    stderr_truncated: bool = False
