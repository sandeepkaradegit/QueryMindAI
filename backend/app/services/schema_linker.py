from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable

from app.schemas.linking import (
    LinkableTable,
    SchemaColumnReference,
    SchemaLinkAmbiguity,
    SchemaLinkResponse,
)


_DESCRIPTION_STOP_WORDS = {
    "a", "an", "and", "as", "at", "by", "for", "from", "in", "of", "on", "or", "the", "to", "with",
}
_NON_PLURAL_SUFFIXES = {"address", "analysis", "business", "cents", "status"}


def _normalize_label(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("_", " ").strip().lower())


def _add_plural_variants(values: set[str], label: str) -> None:
    normalized = _normalize_label(label)
    if not normalized:
        return
    values.add(normalized)
    words = normalized.split()
    last_word = words[-1]
    if last_word in _NON_PLURAL_SUFFIXES:
        return
    if last_word.endswith("s") and len(last_word) > 3:
        words[-1] = last_word[:-1]
    else:
        words[-1] = f"{last_word}s"
    values.add(" ".join(words))


def _table_name_variants(name: str) -> set[str]:
    normalized = _normalize_label(name)
    variants = {normalized}
    words = normalized.split()
    if not words:
        return variants
    last = words[-1]
    if last.endswith("s") and len(last) > 3:
        words[-1] = last[:-1]
        variants.add(" ".join(words))
    elif len(last) > 2:
        words[-1] = f"{last}s"
        variants.add(" ".join(words))
    return variants


def _aliases(name: str, description: str | None, synonyms: Iterable[str], *, is_column: bool) -> set[str]:
    values: set[str] = set()
    _add_plural_variants(values, name)

    normalized_name = _normalize_label(name)
    if normalized_name.endswith("s") and len(normalized_name) > 3:
        _add_plural_variants(values, normalized_name[:-1])
    if is_column and normalized_name.endswith(" cents"):
        _add_plural_variants(values, normalized_name.removesuffix(" cents"))

    for synonym in synonyms:
        _add_plural_variants(values, synonym)

    if description:
        if is_column:
            for token in re.findall(r"[a-z0-9]+", description.lower()):
                if len(token) > 2 and token not in _DESCRIPTION_STOP_WORDS:
                    _add_plural_variants(values, token)
        else:
            _add_plural_variants(values, description)

    return {value for value in values if value}


def _matching_spans(question: str, aliases: Iterable[str]) -> list[tuple[int, int]]:
    matches: set[tuple[int, int]] = set()
    for alias in aliases:
        phrase = r"\s+".join(re.escape(part) for part in alias.split())
        for match in re.finditer(rf"(?<!\w){phrase}(?!\w)", question, re.IGNORECASE):
            matches.add(match.span())
    return sorted(matches)


def _remove_nested_spans(spans: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    selected: list[tuple[int, int]] = []
    for start, end in sorted(spans, key=lambda span: (-(span[1] - span[0]), span[0], span[1])):
        if any(existing_start <= start and end <= existing_end for existing_start, existing_end in selected):
            continue
        selected.append((start, end))
    return sorted(selected)


def link_schema(question: str, schema_tables: list[LinkableTable]) -> SchemaLinkResponse:
    explicit_tables = {
        table.name
        for table in schema_tables
        if _matching_spans(
            question,
            _aliases(table.name, table.description, table.synonyms, is_column=False),
        )
    }

    column_matches: dict[tuple[int, int], set[SchemaColumnReference]] = defaultdict(set)
    for table in schema_tables:
        for column in table.columns:
            reference = SchemaColumnReference(table_name=table.name, column_name=column.name)
            aliases = _aliases(column.name, column.description, column.synonyms, is_column=True)
            for span in _matching_spans(question, aliases):
                column_matches[span].add(reference)

    retained_spans = _remove_nested_spans(column_matches)
    resolved_columns: set[SchemaColumnReference] = set()
    ambiguities: list[SchemaLinkAmbiguity] = []
    table_term_variants = {
        table.name: _table_name_variants(table.name)
        for table in schema_tables
    }

    for start, end in retained_spans:
        candidates = column_matches[(start, end)]
        contextual_candidates = candidates & {
            candidate for candidate in candidates if candidate.table_name in explicit_tables
        }
        if contextual_candidates:
            candidates = contextual_candidates

        ordered_candidates = sorted(candidates, key=lambda candidate: (candidate.table_name, candidate.column_name))
        if len(ordered_candidates) == 1:
            resolved_columns.add(ordered_candidates[0])
        elif ordered_candidates:
            normalized_term = _normalize_label(question[start:end])
            candidate_tables = {candidate.table_name for candidate in ordered_candidates}
            if (
                len(candidate_tables) == 1
                and (table_name := next(iter(candidate_tables))) in explicit_tables
                and normalized_term in table_term_variants.get(table_name, set())
            ):
                continue
            ambiguities.append(
                SchemaLinkAmbiguity(
                    term=question[start:end],
                    candidates=ordered_candidates,
                )
            )

    resolved_tables = set(explicit_tables)
    resolved_tables.update(column.table_name for column in resolved_columns)

    return SchemaLinkResponse(
        resolved_tables=sorted(resolved_tables),
        resolved_columns=sorted(resolved_columns, key=lambda column: (column.table_name, column.column_name)),
        ambiguities=ambiguities,
    )