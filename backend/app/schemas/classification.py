from enum import Enum

from pydantic import BaseModel


class QueryType(str, Enum):
    SELECT_SIMPLE = "SELECT_SIMPLE"
    SELECT_AGGREGATE = "SELECT_AGGREGATE"
    SELECT_JOIN = "SELECT_JOIN"
    SELECT_TEMPORAL = "SELECT_TEMPORAL"
    UNSUPPORTED = "UNSUPPORTED"


class QueryClassification(BaseModel):
    query_type: QueryType
    tables_mentioned: list[str]
    columns_mentioned: list[str]