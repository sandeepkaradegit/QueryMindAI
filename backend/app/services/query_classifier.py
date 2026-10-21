from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from app.schemas.classification import QueryClassification, QueryType


_UNSUPPORTED_INTENT = re.compile(
    r"\b(?:delete|drop|truncate|alter|insert|update|replace|merge|"
    r"grant|revoke|execute|exec|remove|erase|destroy)\b"
    r"|\bcreate\s+(?:table|view|index|database|schema|trigger|procedure)\b",
    re.IGNORECASE,
)
_TEMPORAL_INTENT = re.compile(
    r"\b(?:date|time|today|yesterday|tomorrow|daily|weekly|monthly|quarterly|yearly|"
    r"before|after|since|between)\b"
    r"|\b(?:last|past|previous|current|this)\s+(?:day|week|month|quarter|year)\b"
    r"|\b(?:day|week|month|quarter|year)s?\b",
    re.IGNORECASE,
)
_AGGREGATE_INTENT = re.compile(
    r"\b(?:count|sum|average|avg|mean|total|revenue|sales|amount|number of|"
    r"how many|how much|top|bottom|highest|lowest|most|least|maximum|minimum)\b",
    re.IGNORECASE,
)
_JOIN_INTENT = re.compile(r"\b(?:join|alongside|together with|associated with)\b", re.IGNORECASE)


def _identifier_variants(identifier: str) -> set[str]:
    normalized = re.sub(r"_+", " ", identifier).strip().lower()
    variants = {normalized}
    if normalized.endswith("s") and len(normalized) > 3:
        variants.add(normalized[:-1])
    return variants


def _mentions_identifier(question: str, identifier: str) -> bool:
    for variant in _identifier_variants(identifier):
        phrase = r"\s+".join(re.escape(part) for part in variant.split())
        if re.search(rf"(?<!\w){phrase}(?!\w)", question, re.IGNORECASE):
            return True
    return False


def _schema_mentions(
    question: str,
    schema_catalog: Mapping[str, Sequence[str]],
) -> tuple[list[str], list[str]]:
    tables_mentioned: list[str] = []
    columns_mentioned: list[str] = []

    for table_name, column_names in schema_catalog.items():
        if _mentions_identifier(question, table_name):
            tables_mentioned.append(table_name)
        for column_name in column_names:
            if _mentions_identifier(question, column_name):
                columns_mentioned.append(column_name)

    return tables_mentioned, columns_mentioned


def classify_question(
    question: str,
    schema_catalog: Mapping[str, Sequence[str]] | None = None,
) -> QueryClassification:
    normalized_question = question.strip()
    tables_mentioned, columns_mentioned = _schema_mentions(
        normalized_question,
        schema_catalog or {},
    )

    if _UNSUPPORTED_INTENT.search(normalized_question):
        query_type = QueryType.UNSUPPORTED
    elif _TEMPORAL_INTENT.search(normalized_question):
        query_type = QueryType.SELECT_TEMPORAL
    elif _AGGREGATE_INTENT.search(normalized_question):
        query_type = QueryType.SELECT_AGGREGATE
    elif len(tables_mentioned) > 1 or _JOIN_INTENT.search(normalized_question):
        query_type = QueryType.SELECT_JOIN
    else:
        query_type = QueryType.SELECT_SIMPLE

    return QueryClassification(
        query_type=query_type,
        tables_mentioned=tables_mentioned,
        columns_mentioned=columns_mentioned,
    )