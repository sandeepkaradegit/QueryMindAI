from pydantic import BaseModel, Field

from app.schemas.classification import QueryType


class ExampleRetrievalRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=3, ge=1, le=5)


class QuestionSqlExample(BaseModel):
    id: str
    question: str = Field(min_length=1)
    sql: str = Field(min_length=1)
    query_type: QueryType


class RetrievedExample(QuestionSqlExample):
    similarity: float = Field(ge=0.0, le=1.0)


class ExampleRetrievalResponse(BaseModel):
    examples: list[RetrievedExample]