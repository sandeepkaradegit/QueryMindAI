from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from app.schemas.examples import QuestionSqlExample, RetrievedExample
from app.schemas.linking import LinkableColumn, LinkableTable
from app.services.schema_linker import link_schema


_CODE_REQUEST = re.compile(
    r"\b(?:python|javascript|typescript|java|c\+\+|c#|programming|source code|script)\b"
    r"|\b(?:write|generate|give|show|create)\s+(?:me\s+)?(?:some\s+)?code\b",
    re.IGNORECASE,
)
_ANALYTICAL_ACTION = re.compile(
    r"\b(?:show|list|find|compare|trend|rank|count|sum|average|avg|mean|total|revenue|sales|"
    r"amount|number of|how many|how much|top|bottom|highest|lowest|most|least|maximum|minimum|"
    r"which|who|when|where)\b",
    re.IGNORECASE,
)
_DIALECT_NAMES = {
    "sqlite": "sqlite",
    "postgresql": "postgres",
    "postgres": "postgres",
    "mysql": "mysql",
}
_EXACT_MATCH_INTENT = re.compile(r"\b(?:exact|exactly|equal|equals|=)\b", re.IGNORECASE)
_TEXTUAL_COLUMN_HINTS = (
    "name",
    "country",
    "region",
    "email",
)


class InputOutOfScopeError(ValueError):
    """Raised when an input is not a database analytics question."""


class SQLGuardrailError(ValueError):
    """Raised when generated SQL fails a safety or relevance check."""


def _is_textual_column(column_name: str) -> bool:
    lowered = column_name.lower()
    if lowered.endswith(("_id", "_at", "_date", "_cents")):
        return False
    return any(hint in lowered for hint in _TEXTUAL_COLUMN_HINTS)


def _column_and_string_literal(expression: exp.Expression) -> tuple[exp.Column, exp.Literal] | None:
    if not isinstance(expression, (exp.EQ, exp.Like, exp.ILike)):
        return None
    left = expression.this
    right = expression.expression
    left_column = left if isinstance(left, exp.Column) else left.this if _lower_or_upper_wrapped(left) and isinstance(left.this, exp.Column) else None
    right_column = right if isinstance(right, exp.Column) else right.this if _lower_or_upper_wrapped(right) and isinstance(right.this, exp.Column) else None

    left_literal = left if isinstance(left, exp.Literal) and left.is_string else left.this if _lower_or_upper_wrapped(left) and isinstance(left.this, exp.Literal) and left.this.is_string else None
    right_literal = right if isinstance(right, exp.Literal) and right.is_string else right.this if _lower_or_upper_wrapped(right) and isinstance(right.this, exp.Literal) and right.this.is_string else None

    if left_column is not None and right_literal is not None:
        return left_column, right_literal
    if right_column is not None and left_literal is not None:
        return right_column, left_literal
    return None


def _lower_or_upper_wrapped(expression: exp.Expression) -> bool:
    return isinstance(expression, (exp.Lower, exp.Upper))


def _as_partial_pattern(value: str) -> str:
    if "%" in value or "_" in value:
        return value
    return f"%{value}%"


def _resolve_column_key(
    column: exp.Column,
    *,
    table_aliases: Mapping[str, str],
    actual_tables: set[str],
    normalized_schema: Mapping[str, set[str]],
) -> str | None:
    column_name = column.name.lower()
    if column.table:
        table_name = table_aliases.get(column.table.lower(), column.table.lower())
        if table_name in normalized_schema and column_name in normalized_schema[table_name]:
            return f"{table_name}.{column_name}"
        return None
    matching_tables = [table_name for table_name in actual_tables if column_name in normalized_schema[table_name]]
    if len(matching_tables) == 1:
        return f"{matching_tables[0]}.{column_name}"
    return None


def _canonicalize_literal_value(
    raw_value: str,
    column_key: str | None,
    value_normalization_map: Mapping[str, Mapping[str, str]],
) -> str:
    if column_key is None:
        return raw_value
    aliases = value_normalization_map.get(column_key)
    if not aliases:
        return raw_value
    lookup = raw_value.strip().lower()
    return aliases.get(lookup, raw_value)


def _normalize_text_filter_matching(
    query: exp.Query,
    question: str,
    *,
    table_aliases: Mapping[str, str],
    actual_tables: set[str],
    normalized_schema: Mapping[str, set[str]],
    value_normalization_map: Mapping[str, Mapping[str, str]],
) -> exp.Query:
    exact_requested = bool(_EXACT_MATCH_INTENT.search(question))
    comparisons = list(query.find_all(exp.EQ)) + list(query.find_all(exp.Like)) + list(query.find_all(exp.ILike))
    for expression in comparisons:
        match = _column_and_string_literal(expression)
        if match is None:
            continue
        column, literal = match
        if not _is_textual_column(column.name):
            continue
        column_key = _resolve_column_key(
            column,
            table_aliases=table_aliases,
            actual_tables=actual_tables,
            normalized_schema=normalized_schema,
        )
        if isinstance(expression, exp.EQ) and exact_requested:
            canonical_literal = exp.Literal.string(
                _canonicalize_literal_value(str(literal.this), column_key, value_normalization_map)
            )
            literal.replace(canonical_literal)
            continue
        if isinstance(expression, exp.ILike):
            canonical_literal = exp.Literal.string(
                _canonicalize_literal_value(str(literal.this), column_key, value_normalization_map)
            )
            literal.replace(canonical_literal)
            continue
        literal_value = _canonicalize_literal_value(str(literal.this), column_key, value_normalization_map)
        normalized_literal = exp.Literal.string(_as_partial_pattern(literal_value))
        normalized_expression = exp.Like(
            this=exp.Lower(this=column.copy()),
            expression=exp.Lower(this=normalized_literal),
        )
        expression.replace(normalized_expression)
    return query


def validate_analytics_question(
    question: str,
    schema_catalog: Mapping[str, Sequence[str]],
) -> None:
    if _CODE_REQUEST.search(question):
        raise InputOutOfScopeError(
            "Please ask a question about data in the configured database, not for code or programming help."
        )

    schema_tables = [
        LinkableTable(
            name=table_name,
            columns=[LinkableColumn(name=column_name) for column_name in column_names],
        )
        for table_name, column_names in schema_catalog.items()
    ]
    linked = link_schema(question, schema_tables)
    if linked.ambiguities:
        return

    has_schema_reference = bool(linked.resolved_tables or linked.resolved_columns)
    has_analytics_intent = bool(_ANALYTICAL_ACTION.search(question))
    if not has_schema_reference and not has_analytics_intent:
        raise InputOutOfScopeError(
            "Please ask an analytics question about data in the configured database."
        )


def _parse_single_read_query(sql: str, dialect: str) -> exp.Query:
    read_dialect = _DIALECT_NAMES.get(dialect.lower())
    if read_dialect is None:
        raise SQLGuardrailError("The configured SQL dialect is not supported by the SQL validator.")
    try:
        parsed_statements = [statement for statement in sqlglot.parse(sql, read=read_dialect) if statement]
    except ParseError:
        raise SQLGuardrailError("The generated SQL could not be parsed for the configured dialect.") from None

    if len(parsed_statements) != 1 or not isinstance(parsed_statements[0], exp.Query):
        raise SQLGuardrailError("Only one read-only SELECT query is allowed.")

    query = parsed_statements[0]
    disallowed_nodes = (exp.Delete, exp.Insert, exp.Update, exp.Create, exp.Drop, exp.Alter, exp.Into, exp.Lock)
    if any(isinstance(node, disallowed_nodes) for node in query.walk()):
        raise SQLGuardrailError("The generated query contains a disallowed write or lock operation.")
    if any(_is_wildcard_projection(projection) for projection in query.selects):
        raise SQLGuardrailError("Wildcard projections are not allowed; select only relevant columns.")
    return query


def _is_wildcard_projection(projection: exp.Expression) -> bool:
    return isinstance(projection, exp.Star) or (
        isinstance(projection, exp.Column) and isinstance(projection.this, exp.Star)
    )


def _query_tables(query: exp.Query) -> tuple[set[str], set[str]]:
    cte_names = {cte.alias_or_name.lower() for cte in query.find_all(exp.CTE)}
    table_names: set[str] = set()
    for table in query.find_all(exp.Table):
        if not table.name or table.name.lower() in cte_names:
            continue
        table_names.add(table.name.lower())
    return table_names, cte_names


def _table_aliases(query: exp.Query) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for table in query.find_all(exp.Table):
        if table.name and table.alias:
            aliases[table.alias.lower()] = table.name.lower()
    return aliases


def _derived_aliases(query: exp.Query) -> set[str]:
    aliases: set[str] = set()
    for subquery in query.find_all(exp.Subquery):
        if subquery.alias:
            aliases.add(subquery.alias.lower())
    return aliases


def _output_aliases(query: exp.Query) -> set[str]:
    aliases = {
        projection.alias_or_name.lower()
        for projection in query.selects
        if projection.alias_or_name
    }
    for cte in query.find_all(exp.CTE):
        cte_query = cte.this
        if isinstance(cte_query, exp.Query):
            aliases.update(
                projection.alias_or_name.lower()
                for projection in cte_query.selects
                if projection.alias_or_name
            )
    return aliases


def _is_cte_or_derived_reference(
    column: exp.Column,
    *,
    cte_names: set[str],
    derived_aliases: set[str],
    table_aliases: Mapping[str, str],
) -> bool:
    if not column.table:
        return False
    table_name = column.table.lower()
    if table_name in cte_names or table_name in derived_aliases:
        return True
    resolved = table_aliases.get(table_name, table_name)
    return resolved in cte_names or resolved in derived_aliases


def _example_projection_columns(
    examples: Sequence[QuestionSqlExample | RetrievedExample],
    dialect: str,
) -> set[str]:
    read_dialect = _DIALECT_NAMES.get(dialect.lower())
    if read_dialect is None:
        return set()
    allowed_columns: set[str] = set()
    for example in examples:
        try:
            statements = [statement for statement in sqlglot.parse(example.sql, read=read_dialect) if statement]
        except ParseError:
            continue
        if len(statements) == 1 and isinstance(statements[0], exp.Query):
            for projection in statements[0].selects:
                allowed_columns.update(column.name.lower() for column in projection.find_all(exp.Column))
    return allowed_columns


def validate_and_sanitize_sql(
    *,
    sql: str,
    dialect: str,
    question: str,
    schema_catalog: Mapping[str, Sequence[str]],
    relevant_tables: Sequence[str],
    relevant_columns: Sequence[tuple[str, str]],
    examples: Sequence[QuestionSqlExample | RetrievedExample],
    max_rows: int,
    value_normalization_map: Mapping[str, Mapping[str, str]] | None = None,
) -> str:
    query = _parse_single_read_query(sql, dialect)
    actual_tables, cte_names = _query_tables(query)
    table_aliases = _table_aliases(query)
    derived_aliases = _derived_aliases(query)
    output_aliases = _output_aliases(query)
    normalized_schema = {
        table_name.lower(): {column.lower() for column in columns}
        for table_name, columns in schema_catalog.items()
    }
    unknown_tables = actual_tables - normalized_schema.keys()
    if unknown_tables:
        raise SQLGuardrailError("The generated query references tables outside the configured schema.")
    if not actual_tables:
        raise SQLGuardrailError("The generated query must read from a table in the configured schema.")

    linked_tables = {table.lower() for table in relevant_tables}
    example_tables: set[str] = set()
    read_dialect = _DIALECT_NAMES[dialect.lower()]
    for example in examples:
        try:
            example_statements = [statement for statement in sqlglot.parse(example.sql, read=read_dialect) if statement]
        except ParseError:
            continue
        if len(example_statements) == 1 and isinstance(example_statements[0], exp.Query):
            example_tables.update(table.name.lower() for table in example_statements[0].find_all(exp.Table))

    allowed_tables = linked_tables | example_tables
    if linked_tables and not (actual_tables & linked_tables):
        raise SQLGuardrailError("The generated query references tables unrelated to the question.")

    referenced_columns = [column for column in query.find_all(exp.Column) if column.name != "*"]
    for column in referenced_columns:
        column_name = column.name.lower()
        if not column.table and column_name in output_aliases:
            continue
        if column.table:
            table_name = column.table.lower()
            if table_name in cte_names:
                continue
            if table_name in derived_aliases:
                continue
            table_name = table_aliases.get(table_name, table_name)
            if table_name in cte_names:
                continue
            if table_name not in normalized_schema or column_name not in normalized_schema[table_name]:
                raise SQLGuardrailError("The generated query references columns outside the configured schema.")
        else:
            matching_tables = [
                table_name
                for table_name in actual_tables
                if column_name in normalized_schema[table_name]
            ]
            if not matching_tables:
                raise SQLGuardrailError("The generated query references columns outside the configured schema.")
            if len(matching_tables) > 1:
                raise SQLGuardrailError("The generated query contains an ambiguous unqualified column.")

    allow_linked_table_projection_fallback = not relevant_columns and bool(linked_tables)
    allowed_projection_columns = {column.lower() for _, column in relevant_columns}
    allowed_projection_columns.update(_example_projection_columns(examples, dialect))
    for projection in query.selects:
        disallowed_projection_columns: set[str] = set()
        for column in projection.find_all(exp.Column):
            column_name = column.name.lower()
            if column_name in allowed_projection_columns:
                continue
            if allow_linked_table_projection_fallback:
                if column.table:
                    table_name = column.table.lower()
                    resolved_table = table_aliases.get(table_name, table_name)
                    if (
                        resolved_table in linked_tables
                        and resolved_table in normalized_schema
                        and column_name in normalized_schema[resolved_table]
                    ):
                        continue
                else:
                    matching_tables = [
                        table_name for table_name in actual_tables if column_name in normalized_schema[table_name]
                    ]
                    if len(matching_tables) == 1 and matching_tables[0] in linked_tables:
                        continue
            if _is_cte_or_derived_reference(
                column,
                cte_names=cte_names,
                derived_aliases=derived_aliases,
                table_aliases=table_aliases,
            ):
                continue
            disallowed_projection_columns.add(column_name)
        if disallowed_projection_columns:
            raise SQLGuardrailError("The generated query selects columns unrelated to the question.")

    for projection in query.selects:
        if _is_wildcard_projection(projection):
            raise SQLGuardrailError("Wildcard projections are not allowed; select only relevant columns.")

    query = _normalize_text_filter_matching(
        query,
        question,
        table_aliases=table_aliases,
        actual_tables=actual_tables,
        normalized_schema=normalized_schema,
        value_normalization_map=value_normalization_map or {},
    )

    safe_max_rows = min(max(max_rows, 1), 1000)
    current_limit = query.args.get("limit")
    if current_limit is None:
        query = query.limit(safe_max_rows)
    else:
        limit_value = current_limit.expression
        if isinstance(limit_value, exp.Literal) and limit_value.is_int:
            if int(limit_value.this) > safe_max_rows:
                query.set("limit", exp.Limit(expression=exp.Literal.number(safe_max_rows)))
        else:
            query.set("limit", exp.Limit(expression=exp.Literal.number(safe_max_rows)))

    return query.sql(dialect=read_dialect)