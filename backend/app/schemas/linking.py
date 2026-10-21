from pydantic import BaseModel, ConfigDict, Field


class LinkableColumn(BaseModel):
    name: str
    description: str | None = None
    synonyms: list[str] = Field(default_factory=list)


class LinkableTable(BaseModel):
    name: str
    description: str | None = None
    synonyms: list[str] = Field(default_factory=list)
    columns: list[LinkableColumn]


class SchemaColumnReference(BaseModel):
    model_config = ConfigDict(frozen=True)

    table_name: str
    column_name: str


class SchemaLinkAmbiguity(BaseModel):
    term: str
    candidates: list[SchemaColumnReference]


class SchemaLinkResponse(BaseModel):
    resolved_tables: list[str]
    resolved_columns: list[SchemaColumnReference]
    ambiguities: list[SchemaLinkAmbiguity]