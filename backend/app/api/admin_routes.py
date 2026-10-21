from __future__ import annotations

from dataclasses import dataclass, field
import logging
from pathlib import Path
import subprocess
import sys
import time

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from app import admin_store
from app.services.example_retriever import load_example_library
from app.services import vector_index
from app.services import semantic_cache
from app.services.llm_providers import PROVIDERS, default_model
from app.schemas.admin import (
    DatabaseConnectionCreateRequest,
    DatabaseConnectionResponse,
    ExampleCreateRequest,
    ExampleResponse,
    ExampleUpdateRequest,
    GuardrailsConfigRequest,
    GuardrailsConfigResponse,
    ModelConfigRequest,
    ModelConfigResponse,
    QueryLogResponse,
    SchemaAnnotationResponse,
    SchemaAnnotationUpsertRequest,
    ScriptDescriptorResponse,
    ScriptOptionResponse,
    ScriptRunRequest,
    ScriptRunResponse,
    ValueNormalizationResponse,
    ValueNormalizationUpsertRequest,
)

router = APIRouter(prefix="/admin", tags=["admin"])
logger = logging.getLogger(__name__)

_SUPPORTED_BACKENDS = {"sqlite", "postgresql", "mysql"}
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT_TIMEOUT_SECONDS = 10 * 60
_MAX_OUTPUT_CHARS = 24_000


@dataclass(frozen=True)
class _ScriptOption:
    id: str
    label: str
    args: tuple[str, ...]
    description: str | None = None


@dataclass(frozen=True)
class _ScriptDefinition:
    id: str
    name: str
    path: Path
    category: str
    description: str
    compatibility: str
    compatibility_note: str
    new_db_flow_order: int | None = None
    new_db_flow_title: str | None = None
    new_db_flow_detail: str | None = None
    new_db_flow_option_id: str | None = None
    options: tuple[_ScriptOption, ...] = field(default_factory=tuple)


def _build_script_definitions() -> dict[str, _ScriptDefinition]:
    scripts_dir = _PROJECT_ROOT / "scripts"
    definitions: list[_ScriptDefinition] = [
        _ScriptDefinition(
            id="seed-db",
            name="Seed demo database",
            path=scripts_dir / "seed_db.py",
            category="database",
            description="Regenerates deterministic demo data in data/querymind.sqlite3.",
            compatibility="demo_only",
            compatibility_note="Uses the current demo schema and fixed row-generation logic.",
            new_db_flow_order=1,
            new_db_flow_title="1) Seed demo DB (or keep your connected DB)",
            new_db_flow_detail="Use this when you want deterministic local test data before validations.",
        ),
        _ScriptDefinition(
            id="run-evaluation",
            name="Run SQL evaluation harness",
            path=scripts_dir / "run_evaluation.py",
            category="evaluation",
            description="Runs the 30-case evaluation dataset and writes report artifacts under evaluations/.",
            compatibility="demo_only",
            compatibility_note="Depends on evaluations/sql_generation_v1.json and current demo-domain examples/schema.",
            options=(
                _ScriptOption(
                    id="offline",
                    label="Offline deterministic run",
                    args=(
                        "--output",
                        "evaluations/report_offline.json",
                        "--report",
                        "evaluations/report_offline.md",
                    ),
                    description="No live LLM calls; deterministic fixture provider.",
                ),
                _ScriptOption(
                    id="live",
                    label="Live model run",
                    args=(
                        "--live",
                        "--output",
                        "evaluations/report_live.json",
                        "--report",
                        "evaluations/report_live.md",
                    ),
                    description="Uses configured live provider (requires model credentials).",
                ),
            ),
        ),
    ]
    for validation_path in sorted(scripts_dir.glob("validate_*.py")):
        validation_name = validation_path.stem.removeprefix("validate_").replace("_", " ")
        definitions.append(
            _ScriptDefinition(
                id=validation_path.stem.replace("_", "-"),
                name=f"Validate: {validation_name}",
                path=validation_path,
                category="validation",
                description=f"Runs {validation_path.name}.",
                compatibility="demo_only",
                compatibility_note="Contains assertions and sample SQL tied to the seeded demo schema.",
            )
        )
    by_id = {definition.id: definition for definition in definitions}

    generic_overrides = {
        "validate-postgres-integration": (
            "generic",
            "Integration check against a provided PostgreSQL URL; not tied to demo table names.",
        ),
    }
    for script_id, (compatibility, note) in generic_overrides.items():
        definition = by_id.get(script_id)
        if definition is None:
            continue
        by_id[script_id] = _ScriptDefinition(
            id=definition.id,
            name=definition.name,
            path=definition.path,
            category=definition.category,
            description=definition.description,
            compatibility=compatibility,
            compatibility_note=note,
            new_db_flow_order=definition.new_db_flow_order,
            new_db_flow_title=definition.new_db_flow_title,
            new_db_flow_detail=definition.new_db_flow_detail,
            new_db_flow_option_id=definition.new_db_flow_option_id,
            options=definition.options,
        )

    flow_overrides = {
        "validate-schema-discovery": (
            2,
            "2) Validate schema readability",
            "Checks schema discovery and relationship introspection.",
            None,
        ),
        "validate-sql-guardrails": (
            3,
            "3) Validate SQL guardrails",
            "Confirms read-only and safety checks before query execution.",
            None,
        ),
        "validate-backend-api": (
            4,
            "4) Validate backend API",
            "Smoke-checks API endpoints against configured database access.",
            None,
        ),
        "run-evaluation": (
            5,
            "5) Run offline evaluation",
            "Generates deterministic baseline report (no live LLM calls).",
            "offline",
        ),
        "run-evaluation-live": (
            6,
            "6) Run live evaluation (optional)",
            "Uses configured model credentials for real accuracy measurement.",
            "live",
        ),
    }
    if "run-evaluation" in by_id:
        definition = by_id["run-evaluation"]
        order, title, detail, option_id = flow_overrides["run-evaluation"]
        by_id["run-evaluation"] = _ScriptDefinition(
            id=definition.id,
            name=definition.name,
            path=definition.path,
            category=definition.category,
            description=definition.description,
            compatibility=definition.compatibility,
            compatibility_note=definition.compatibility_note,
            new_db_flow_order=order,
            new_db_flow_title=title,
            new_db_flow_detail=detail,
            new_db_flow_option_id=option_id,
            options=definition.options,
        )
    for script_id, flow in flow_overrides.items():
        if script_id in {"run-evaluation", "run-evaluation-live"}:
            continue
        definition = by_id.get(script_id)
        if definition is None:
            continue
        order, title, detail, option_id = flow
        by_id[script_id] = _ScriptDefinition(
            id=definition.id,
            name=definition.name,
            path=definition.path,
            category=definition.category,
            description=definition.description,
            compatibility=definition.compatibility,
            compatibility_note=definition.compatibility_note,
            new_db_flow_order=order,
            new_db_flow_title=title,
            new_db_flow_detail=detail,
            new_db_flow_option_id=option_id,
            options=definition.options,
        )

    return by_id


_SCRIPT_DEFINITIONS = _build_script_definitions()


def _mask_connection(record: admin_store.DatabaseConnectionRecord) -> DatabaseConnectionResponse:
    try:
        masked_url = make_url(record.connection_url).render_as_string(hide_password=True)
    except Exception:
        masked_url = "***"
    return DatabaseConnectionResponse(
        id=record.id,
        name=record.name,
        dialect=record.dialect,
        connection_url=masked_url,
        is_active=record.is_active,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


@router.get("/database", response_model=list[DatabaseConnectionResponse])
def list_database_connections() -> list[DatabaseConnectionResponse]:
    return [_mask_connection(record) for record in admin_store.list_database_connections()]


@router.post(
    "/database",
    response_model=DatabaseConnectionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_database_connection(request: DatabaseConnectionCreateRequest) -> DatabaseConnectionResponse:
    try:
        url = make_url(request.connection_url)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="The connection URL could not be parsed.",
        ) from None

    backend_name = url.get_backend_name().split("+")[0]
    if backend_name not in _SUPPORTED_BACKENDS or backend_name != request.dialect:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="The connection URL backend must match the declared dialect (sqlite, postgresql, or mysql).",
        )

    test_engine = create_engine(url)
    try:
        with test_engine.connect():
            pass
    except SQLAlchemyError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="The database connection could not be established. Check the connection URL and server availability.",
        ) from None
    finally:
        test_engine.dispose()

    try:
        record = admin_store.create_database_connection(
            name=request.name,
            dialect=request.dialect,
            connection_url=request.connection_url,
        )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A database connection with this name already exists.",
        ) from None
    return _mask_connection(record)


@router.post("/database/{connection_id}/activate", response_model=DatabaseConnectionResponse)
def activate_database_connection(connection_id: int) -> DatabaseConnectionResponse:
    record = admin_store.activate_database_connection(connection_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Database connection not found.")
    return _mask_connection(record)


@router.delete("/database/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_database_connection(connection_id: int) -> None:
    if not admin_store.delete_database_connection(connection_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Database connection not found.")


def _annotation_to_response(record: admin_store.SchemaAnnotationRecord) -> SchemaAnnotationResponse:
    return SchemaAnnotationResponse(
        id=record.id,
        table_name=record.table_name,
        column_name=record.column_name,
        description=record.description,
        synonyms=record.synonyms,
        is_sensitive=record.is_sensitive,
        updated_at=record.updated_at,
    )


@router.get("/schema/annotations", response_model=list[SchemaAnnotationResponse])
def list_schema_annotations() -> list[SchemaAnnotationResponse]:
    return [_annotation_to_response(record) for record in admin_store.list_schema_annotations()]


@router.put("/schema/annotations", response_model=SchemaAnnotationResponse)
def upsert_schema_annotation(request: SchemaAnnotationUpsertRequest) -> SchemaAnnotationResponse:
    record = admin_store.upsert_schema_annotation(
        table_name=request.table_name,
        column_name=request.column_name,
        description=request.description,
        synonyms=request.synonyms,
        is_sensitive=request.is_sensitive,
    )
    semantic_cache.clear()
    return _annotation_to_response(record)


@router.delete("/schema/annotations/{annotation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_schema_annotation(annotation_id: int) -> None:
    if not admin_store.delete_schema_annotation(annotation_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Schema annotation not found.")
    semantic_cache.clear()


def _normalization_to_response(record: admin_store.ValueNormalizationRecord) -> ValueNormalizationResponse:
    return ValueNormalizationResponse(
        id=record.id,
        table_name=record.table_name,
        column_name=record.column_name,
        alias_value=record.alias_value,
        canonical_value=record.canonical_value,
        updated_at=record.updated_at,
    )


@router.get("/schema/value-normalizations", response_model=list[ValueNormalizationResponse])
def list_value_normalizations(table_name: str | None = None) -> list[ValueNormalizationResponse]:
    return [_normalization_to_response(record) for record in admin_store.list_value_normalizations(table_name=table_name)]


@router.put("/schema/value-normalizations", response_model=ValueNormalizationResponse)
def upsert_value_normalization(request: ValueNormalizationUpsertRequest) -> ValueNormalizationResponse:
    record = admin_store.upsert_value_normalization(
        table_name=request.table_name,
        column_name=request.column_name,
        alias_value=request.alias_value,
        canonical_value=request.canonical_value,
    )
    semantic_cache.clear()
    return _normalization_to_response(record)


@router.delete("/schema/value-normalizations/{normalization_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_value_normalization(normalization_id: int) -> None:
    if not admin_store.delete_value_normalization(normalization_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Value normalization not found.")
    semantic_cache.clear()


def _model_config_response(record: admin_store.ModelConfigRecord) -> ModelConfigResponse:
    active = admin_store.get_active_database_connection()
    return ModelConfigResponse(
        provider=record.provider,
        model=record.model,
        guardrail_model=record.guardrail_model,
        temperature=record.temperature,
        max_tokens=record.max_tokens,
        updated_at=record.updated_at,
        transpile_dialect=record.transpile_dialect,
        database_dialect=active.dialect if active else "sqlite",
        default_models={provider: default_model(provider) for provider in PROVIDERS},
    )


@router.get("/model-config", response_model=ModelConfigResponse)
@router.get("/config", response_model=ModelConfigResponse)
def get_model_config() -> ModelConfigResponse:
    return _model_config_response(admin_store.get_model_config())


@router.put("/model-config", response_model=ModelConfigResponse)
@router.put("/config", response_model=ModelConfigResponse)
def update_model_config(request: ModelConfigRequest) -> ModelConfigResponse:
    record = admin_store.update_model_config(
        provider=request.provider,
        model=request.model,
        guardrail_model=request.guardrail_model,
        temperature=request.temperature,
        max_tokens=request.max_tokens,
        transpile_dialect=request.transpile_dialect,
    )
    return _model_config_response(record)


def _example_to_response(record: admin_store.ExampleRecord) -> ExampleResponse:
    return ExampleResponse(
        id=record.id,
        question=record.question,
        sql=record.sql,
        query_type=record.query_type,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


@router.get("/examples", response_model=list[ExampleResponse])
def list_examples(search: str | None = None, semantic: bool = False) -> list[ExampleResponse]:
    load_example_library()  # ensures the starter library is seeded before the first read
    records = admin_store.list_examples()
    if search and semantic:
        hits = vector_index.search(
            search,
            [(record.id, record.question) for record in records],
            top_k=len(records),
            threshold=0.0,
        )
        if hits is not None:
            by_id = {record.id: record for record in records}
            return [_example_to_response(by_id[example_id]) for example_id, _ in hits]
    if search:
        needle = search.lower()
        records = [
            record
            for record in records
            if needle in record.question.lower() or needle in record.sql.lower()
        ]
    return [_example_to_response(record) for record in records]


@router.post("/examples", response_model=ExampleResponse, status_code=status.HTTP_201_CREATED)
def create_example(request: ExampleCreateRequest) -> ExampleResponse:
    try:
        record = admin_store.create_example(
            id=request.id,
            question=request.question,
            sql=request.sql,
            query_type=request.query_type.value,
        )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An example with this ID already exists.",
        ) from None
    return _example_to_response(record)


@router.put("/examples/{example_id}", response_model=ExampleResponse)
def update_example(example_id: str, request: ExampleUpdateRequest) -> ExampleResponse:
    record = admin_store.update_example(
        example_id,
        question=request.question,
        sql=request.sql,
        query_type=request.query_type.value,
    )
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Example not found.")
    return _example_to_response(record)


@router.delete("/examples/{example_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_example(example_id: str) -> None:
    if not admin_store.delete_example(example_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Example not found.")


@router.get("/logs", response_model=list[QueryLogResponse])
def list_query_logs(
    status_filter: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[QueryLogResponse]:
    limit = min(max(limit, 1), 500)
    offset = max(offset, 0)
    records = admin_store.list_query_logs(status_filter=status_filter, limit=limit, offset=offset)
    return [
        QueryLogResponse(
            id=record.id,
            trace_id=record.trace_id,
            question=record.question,
            status=record.status,
            sql=record.sql,
            error_reason=record.error_reason,
            row_count=record.row_count,
            latency_ms=record.latency_ms,
            created_at=record.created_at,
        )
        for record in records
    ]


@router.get("/guardrails", response_model=GuardrailsConfigResponse)
def get_guardrails_config() -> GuardrailsConfigResponse:
    record = admin_store.get_guardrails_config()
    return GuardrailsConfigResponse(
        max_rows=record.max_rows,
        allow_write_operations=record.allow_write_operations,
        allowed_table_prefixes=record.allowed_table_prefixes,
        updated_at=record.updated_at,
    )


@router.put("/guardrails", response_model=GuardrailsConfigResponse)
def update_guardrails_config(request: GuardrailsConfigRequest) -> GuardrailsConfigResponse:
    if request.allow_write_operations:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "Destructive SQL operations cannot be enabled through this API. "
                "Read-only enforcement is a non-configurable safety boundary."
            ),
        )
    record = admin_store.update_guardrails_config(
        max_rows=request.max_rows,
        allowed_table_prefixes=request.allowed_table_prefixes,
    )
    return GuardrailsConfigResponse(
        max_rows=record.max_rows,
        allow_write_operations=record.allow_write_operations,
        allowed_table_prefixes=record.allowed_table_prefixes,
        updated_at=record.updated_at,
    )


def _truncate_output(output: str) -> tuple[str, bool]:
    if len(output) <= _MAX_OUTPUT_CHARS:
        return output, False
    omitted_count = len(output) - _MAX_OUTPUT_CHARS
    return (
        f"{output[:_MAX_OUTPUT_CHARS]}\n\n[output truncated: omitted {omitted_count} characters]",
        True,
    )


@router.get("/scripts", response_model=list[ScriptDescriptorResponse])
def list_script_commands() -> list[ScriptDescriptorResponse]:
    return [
        ScriptDescriptorResponse(
            id=definition.id,
            name=definition.name,
            description=definition.description,
            category=definition.category,
            compatibility=definition.compatibility,
            compatibility_note=definition.compatibility_note,
            new_db_flow_order=definition.new_db_flow_order,
            new_db_flow_title=definition.new_db_flow_title,
            new_db_flow_detail=definition.new_db_flow_detail,
            new_db_flow_option_id=definition.new_db_flow_option_id,
            options=[
                ScriptOptionResponse(id=option.id, label=option.label, description=option.description)
                for option in definition.options
            ],
        )
        for definition in _SCRIPT_DEFINITIONS.values()
    ]


@router.post("/scripts/run", response_model=ScriptRunResponse)
def run_script_command(request: ScriptRunRequest) -> ScriptRunResponse:
    definition = _SCRIPT_DEFINITIONS.get(request.script_id)
    if definition is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown script ID.")
    if not definition.path.exists():
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Script file is missing: {definition.path.name}.",
        )

    selected_option = None
    if definition.options:
        if request.option_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="This script requires an option_id.",
            )
        selected_option = next((option for option in definition.options if option.id == request.option_id), None)
        if selected_option is None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Invalid option_id.")
    elif request.option_id is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="This script does not accept an option_id.",
        )

    command = [sys.executable, str(definition.path)]
    if selected_option is not None:
        command.extend(selected_option.args)

    started_at = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            cwd=_PROJECT_ROOT,
            text=True,
            capture_output=True,
            timeout=_SCRIPT_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        partial_stdout = error.stdout if isinstance(error.stdout, str) else ""
        partial_stderr = error.stderr if isinstance(error.stderr, str) else ""
        stdout, stdout_truncated = _truncate_output(partial_stdout)
        stderr, stderr_truncated = _truncate_output(partial_stderr)
        return ScriptRunResponse(
            script_id=request.script_id,
            option_id=request.option_id,
            command=command,
            succeeded=False,
            exit_code=124,
            duration_ms=elapsed_ms,
            stdout=stdout,
            stderr=f"{stderr}\n\nTimed out after {_SCRIPT_TIMEOUT_SECONDS} seconds.".strip(),
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
        )

    elapsed_ms = int((time.perf_counter() - started_at) * 1000)
    stdout, stdout_truncated = _truncate_output(completed.stdout)
    stderr, stderr_truncated = _truncate_output(completed.stderr)
    return ScriptRunResponse(
        script_id=request.script_id,
        option_id=request.option_id,
        command=command,
        succeeded=completed.returncode == 0,
        exit_code=completed.returncode,
        duration_ms=elapsed_ms,
        stdout=stdout,
        stderr=stderr,
        stdout_truncated=stdout_truncated,
        stderr_truncated=stderr_truncated,
    )
