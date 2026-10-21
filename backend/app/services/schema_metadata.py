from __future__ import annotations

from collections.abc import Mapping, Sequence

from app import admin_store
from app.schemas.linking import LinkableColumn, LinkableTable


def build_annotated_schema(
    raw_schema_catalog: Mapping[str, Sequence[str]],
) -> tuple[dict[str, list[str]], list[LinkableTable], dict[str, dict[str, str]]]:
    """Filter sensitive columns out of the schema and attach admin-managed descriptions/synonyms."""
    table_annotations: dict[str, admin_store.SchemaAnnotationRecord] = {}
    column_annotations: dict[tuple[str, str], admin_store.SchemaAnnotationRecord] = {}
    for annotation in admin_store.list_schema_annotations():
        if annotation.column_name:
            column_annotations[(annotation.table_name, annotation.column_name)] = annotation
        else:
            table_annotations[annotation.table_name] = annotation

    filtered_catalog: dict[str, list[str]] = {}
    linkable_tables: list[LinkableTable] = []
    value_normalization_map: dict[str, dict[str, str]] = {}
    for table_name, column_names in raw_schema_catalog.items():
        visible_columns: list[str] = []
        linkable_columns: list[LinkableColumn] = []
        for column_name in column_names:
            column_annotation = column_annotations.get((table_name, column_name))
            if column_annotation is not None and column_annotation.is_sensitive:
                continue
            visible_columns.append(column_name)
            linkable_columns.append(
                LinkableColumn(
                    name=column_name,
                    description=column_annotation.description if column_annotation else None,
                    synonyms=column_annotation.synonyms if column_annotation else [],
                )
            )
        filtered_catalog[table_name] = visible_columns
        table_annotation = table_annotations.get(table_name)
        linkable_tables.append(
            LinkableTable(
                name=table_name,
                description=table_annotation.description if table_annotation else None,
                synonyms=table_annotation.synonyms if table_annotation else [],
                columns=linkable_columns,
            )
        )
    for normalization in admin_store.list_value_normalizations():
        key = f"{normalization.table_name}.{normalization.column_name}"
        value_normalization_map.setdefault(key, {})[normalization.alias_value] = normalization.canonical_value
    return filtered_catalog, linkable_tables, value_normalization_map
