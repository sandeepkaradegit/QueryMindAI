from typing import Literal

from pydantic import BaseModel, Field


class ColumnMetadata(BaseModel):
    name: str
    data_type: str
    nullable: bool
    primary_key: bool


class ForeignKeyMetadata(BaseModel):
    columns: list[str]
    referred_schema: str | None
    referred_table: str
    referred_columns: list[str]


class TableMetadata(BaseModel):
    name: str
    schema_name: str | None = Field(alias="schema")
    kind: Literal["table", "view"]
    columns: list[ColumnMetadata]
    foreign_keys: list[ForeignKeyMetadata]


class DatabaseSchemaResponse(BaseModel):
    dialect: str
    default_schema: str | None
    tables: list[TableMetadata]