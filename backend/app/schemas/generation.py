from pydantic import BaseModel, Field

from app.schemas.classification import QueryType


class SQLGenerationResponse(BaseModel):
    sql: str = Field(min_length=1)
    explanation: str = Field(min_length=1)
    model: str
    query_type: QueryType
    examples_used: list[str]
    transpiled_sql: str | None = None