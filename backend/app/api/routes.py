from collections.abc import Callable
from contextlib import nullcontext
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import inspect
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from app.config import (
    get_groq_api_key,
    get_groq_guardrail_model,
    get_groq_model,
    get_query_timeout_seconds,
)
from app.database import get_database_engine
from app import admin_store
from app.schemas.classification import QueryClassification
from app.schemas.database import (
    ColumnMetadata,
    DatabaseSchemaResponse,
    ForeignKeyMetadata,
    TableMetadata,
)
from app.schemas.examples import ExampleRetrievalRequest, ExampleRetrievalResponse
from app.schemas.generation import SQLGenerationResponse
from app.schemas.linking import LinkableColumn, LinkableTable, SchemaLinkResponse
from app.schemas.query import HealthResponse, QueryRequest, QueryResponse
from app.observability import get_current_trace
from app.services.query_classifier import classify_question
from app.services.example_retriever import load_example_library, rerank_examples, retrieve_examples
from app.services.result_formatter import format_query_result
from app.services.schema_linker import link_schema
from app.services.schema_metadata import build_annotated_schema
from app.services import semantic_cache
from app.services.sql_executor import (
    QueryExecutionError,
    QueryExecutionTimeoutError,
    execute_validated_sql,
)
from app.services.sql_generator import (
    AmbiguousQuestionError,
    CompletionProvider,
    GroqCompletionProvider,
    InvalidGenerationResponseError,
    ProviderUnavailableError,
    UnsupportedQuestionError,
    generate_sql,
)
from app.services.sql_guardrails import InputOutOfScopeError
from app.services.llm_providers import ProviderNotConfiguredError, build_provider, default_model
import sqlglot
from app.services.openevals_guardrails import (
    GuardrailVerdict,
    GuardrailBlockedError,
    GuardrailSetupError,
    OpenEvalsGroqGuardrail,
    SafetyGuardrail,
    blocked_verdict,
    parse_guardrail_verdict,
)
router = APIRouter()
health_router = APIRouter()
logger = logging.getLogger(__name__)


def _evaluate_guardrail(evaluator: Callable[..., object], **kwargs: object) -> GuardrailVerdict:
    try:
        result = evaluator(**kwargs)
    except Exception:
        return blocked_verdict("evaluation_error")
    return parse_guardrail_verdict(result)


def _evaluate_traced_guardrail(
    *,
    stage: str,
    evaluator: Callable[..., object],
    trace,
    **inputs: object,
) -> GuardrailVerdict:
    if trace is None:
        return _evaluate_guardrail(evaluator, **inputs)
    with trace.span(f"{stage}_guardrail", inputs=inputs) as span:
        verdict = _evaluate_guardrail(evaluator, **inputs)
        evaluator_owner = getattr(evaluator, "__self__", None)
        usage = getattr(evaluator_owner, "last_usage", None)
        if hasattr(evaluator_owner, "last_usage"):
            trace.add_token_usage(usage)
        span.set_outputs(verdict=verdict.verdict, reason_code=verdict.reason_code, token_usage=usage)
    trace.record_event(
        "guardrail_decision",
        stage=stage,
        verdict=verdict.verdict,
        reason_code=verdict.reason_code,
    )
    return verdict


def _transpile(sql: str, source_dialect: str, target: str | None) -> str | None:
    if not target:
        return None
    read = {"postgresql": "postgres"}.get(source_dialect, source_dialect)
    write = {"postgresql": "postgres"}.get(target, target)
    try:
        return sqlglot.transpile(sql, read=read, write=write)[0]
    except Exception:
        return None


def _user_safe_sql_validation_message(error: InvalidGenerationResponseError) -> str:
    reason = str(error).lower()
    if "unrelated to the question" in reason:
        return "Generated query included fields unrelated to your question. Please specify which columns you want."
    if "columns outside the configured schema" in reason or "tables outside the configured schema" in reason:
        return "Generated query referenced unavailable schema fields. Please rephrase using known tables or columns."
    if "ambiguous unqualified column" in reason:
        return "Generated query used ambiguous columns. Please specify the table or field more clearly."
    return "Generated query could not be validated safely. Please rephrase with clearer fields or filters."


def _guardrail_block_http_response(error: GuardrailBlockedError) -> tuple[int, str]:
    transient_reasons = {"evaluation_error", "malformed_verdict"}
    if error.reason_code in transient_reasons:
        return (
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Safety check service is temporarily unavailable. Please retry.",
        )
    return (
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        "Request could not be completed safely.",
    )


def get_sql_completion_provider() -> Callable[[], CompletionProvider]:
    def create_provider() -> CompletionProvider:
        model_config = admin_store.get_model_config()
        try:
            return build_provider(
                model_config.provider,
                temperature=model_config.temperature,
                max_tokens=model_config.max_tokens,
            )
        except ProviderNotConfiguredError as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(error),
            ) from None

    return create_provider


def get_safety_guardrail() -> Callable[[], SafetyGuardrail]:
    def create_guardrail() -> SafetyGuardrail:
        api_key = get_groq_api_key()
        if api_key is None:
            raise GuardrailSetupError("Groq credentials are not configured for the safety judge.")
        guardrail_model = admin_store.get_model_config().guardrail_model or get_groq_guardrail_model()
        return OpenEvalsGroqGuardrail(api_key=api_key, model=guardrail_model)

    return create_guardrail


def _schema_catalog(engine: Engine) -> dict[str, list[str]]:
    inspector = inspect(engine)
    schema_name = inspector.default_schema_name
    table_names = set(inspector.get_table_names(schema=schema_name))
    view_names = set(inspector.get_view_names(schema=schema_name))
    return {
        table_name: [
            column["name"]
            for column in inspector.get_columns(table_name, schema=schema_name)
        ]
        for table_name in sorted(table_names | view_names)
    }


def _apply_table_prefix_scope(schema_catalog: dict[str, list[str]]) -> dict[str, list[str]]:
    allowed_prefixes = admin_store.get_guardrails_config().allowed_table_prefixes
    if not allowed_prefixes:
        return schema_catalog
    return {
        table_name: columns
        for table_name, columns in schema_catalog.items()
        if any(table_name.startswith(prefix) for prefix in allowed_prefixes)
    }


def _effective_max_rows() -> int:
    # The admin-configured row cap takes precedence over the QUERYMIND_MAX_ROWS default.
    return admin_store.get_guardrails_config().max_rows


def _log_status_for_http_error(status_code: int) -> str:
    if status_code == status.HTTP_409_CONFLICT:
        return "clarify"
    if status_code in (status.HTTP_422_UNPROCESSABLE_CONTENT, status.HTTP_503_SERVICE_UNAVAILABLE):
        return "refused"
    return "error"


def _schema_relationships(
    engine: Engine,
    schema_catalog: dict[str, list[str]] | None = None,
) -> list[dict[str, object]]:
    inspector = inspect(engine)
    schema_name = inspector.default_schema_name
    visible_schema = schema_catalog if schema_catalog is not None else _schema_catalog(engine)
    table_names = set(inspector.get_table_names(schema=schema_name))
    view_names = set(inspector.get_view_names(schema=schema_name))
    relationships = []
    for table_name in sorted((table_names | view_names) & visible_schema.keys()):
        for foreign_key in inspector.get_foreign_keys(table_name, schema=schema_name):
            referred_table = foreign_key["referred_table"]
            source_columns = foreign_key.get("constrained_columns") or []
            referred_columns = foreign_key.get("referred_columns") or []
            if referred_table not in visible_schema:
                continue
            if not set(source_columns) <= set(visible_schema[table_name]):
                continue
            if not set(referred_columns) <= set(visible_schema[referred_table]):
                continue
            relationships.append(
                {
                    "table_name": table_name,
                    "columns": source_columns,
                    "referred_schema": foreign_key.get("referred_schema"),
                    "referred_table": referred_table,
                    "referred_columns": referred_columns,
                }
            )
    return relationships


@health_router.get("/health", response_model=HealthResponse, tags=["system"])
def health_check() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get("/admin/schema", response_model=DatabaseSchemaResponse, tags=["admin"])
def inspect_database_schema(
    engine: Engine = Depends(get_database_engine),
) -> DatabaseSchemaResponse:
    try:
        inspector = inspect(engine)
        schema_name = inspector.default_schema_name
        table_names = set(inspector.get_table_names(schema=schema_name))
        view_names = set(inspector.get_view_names(schema=schema_name))
        tables = []

        for table_name in sorted(table_names | view_names):
            columns = inspector.get_columns(table_name, schema=schema_name)
            primary_key_columns = set(
                inspector.get_pk_constraint(table_name, schema=schema_name).get("constrained_columns") or []
            )
            foreign_keys = [
                foreign_key
                for foreign_key in inspector.get_foreign_keys(table_name, schema=schema_name)
            ]
            tables.append(
                TableMetadata(
                    name=table_name,
                    schema=schema_name,
                    kind="table" if table_name in table_names else "view",
                    columns=[
                        ColumnMetadata(
                            name=column["name"],
                            data_type=str(column["type"]),
                            nullable=column["nullable"] and column["name"] not in primary_key_columns,
                            primary_key=column["name"] in primary_key_columns,
                        )
                        for column in columns
                    ],
                    foreign_keys=[
                        ForeignKeyMetadata(
                            columns=foreign_key.get("constrained_columns") or [],
                            referred_schema=foreign_key.get("referred_schema"),
                            referred_table=foreign_key["referred_table"],
                            referred_columns=foreign_key.get("referred_columns") or [],
                        )
                        for foreign_key in foreign_keys
                    ],
                )
            )

        return DatabaseSchemaResponse(
            dialect=engine.dialect.name,
            default_schema=schema_name,
            tables=tables,
        )
    except SQLAlchemyError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The configured database schema could not be inspected.",
        ) from None


@router.post(
    "/query/classify",
    response_model=QueryClassification,
    tags=["query"],
)
def classify_query(
    request: QueryRequest,
    engine: Engine = Depends(get_database_engine),
) -> QueryClassification:
    trace = get_current_trace()
    if trace is not None:
        trace.update_inputs(question=request.question)
    try:
        schema_catalog, _, _ = build_annotated_schema(_apply_table_prefix_scope(_schema_catalog(engine)))
    except SQLAlchemyError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The configured database schema could not be inspected.",
        ) from None
    return classify_question(request.question, schema_catalog)


@router.post("/query/link", response_model=SchemaLinkResponse, tags=["query"])
def link_query_schema(
    request: QueryRequest,
    engine: Engine = Depends(get_database_engine),
) -> SchemaLinkResponse:
    trace = get_current_trace()
    if trace is not None:
        trace.update_inputs(question=request.question)
    try:
        _, linkable_schema, _ = build_annotated_schema(_apply_table_prefix_scope(_schema_catalog(engine)))
    except SQLAlchemyError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The configured database schema could not be inspected.",
        ) from None
    return link_schema(request.question, linkable_schema)


@router.post(
    "/query/examples/retrieve",
    response_model=ExampleRetrievalResponse,
    tags=["query"],
)
def retrieve_query_examples(
    request: ExampleRetrievalRequest,
    engine: Engine = Depends(get_database_engine),
) -> ExampleRetrievalResponse:
    trace = get_current_trace()
    if trace is not None:
        trace.update_inputs(question=request.question, requested_top_k=request.top_k)
    try:
        examples = load_example_library()
    except (OSError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The curated example library could not be loaded.",
        ) from None
    with trace.span("example_retrieval", inputs={"question": request.question, "candidate_limit": 10}) if trace else nullcontext() as span:
        candidates = retrieve_examples(request.question, examples, top_k=10)
        if span:
            span.set_outputs(
                candidates=[
                    {"id": example.id, "question": example.question, "sql": example.sql, "score": example.similarity}
                    for example in candidates
                ]
            )
    with trace.span(
        "example_reranking",
        inputs={"question": request.question, "candidate_ids": [example.id for example in candidates]},
    ) if trace else nullcontext() as span:
        ranked_examples, rerank_results = rerank_examples(request.question, candidates, request.top_k)
        if span:
            span.set_outputs(
                reranked=[
                    {
                        "id": result.example.id,
                        "retrieval_score": result.retrieval_score,
                        "rerank_score": result.rerank_score,
                    }
                    for result in rerank_results
                ],
                selected_ids=[example.id for example in ranked_examples],
            )
    return ExampleRetrievalResponse(examples=ranked_examples)


def _generate_sql_or_raise(
    request: QueryRequest,
    *,
    engine: Engine,
    provider_factory: Callable[[], CompletionProvider],
    guardrail_factory: Callable[[], SafetyGuardrail],
    trace,
    failed_sql: str | None = None,
    execution_error_feedback: str | None = None,
) -> SQLGenerationResponse:
    guardrail = None
    if trace is not None:
        trace.update_inputs(
            question=request.question,
            conversation_history_count=len(request.conversation_history),
        )
    try:
        raw_schema_catalog = _apply_table_prefix_scope(_schema_catalog(engine))
        schema_catalog, linkable_schema, value_normalization_map = build_annotated_schema(raw_schema_catalog)
        examples = load_example_library()
        if trace is not None:
            trace.record_event("request_context_loaded", schema=schema_catalog, example_count=len(examples))
        guardrail = guardrail_factory()
        input_verdict = _evaluate_traced_guardrail(
            stage="input_safety",
            trace=trace,
            evaluator=guardrail.evaluate_input,
            question=request.question,
            schema_catalog=schema_catalog,
        )
        if input_verdict.verdict == "block":
            raise GuardrailBlockedError("input", input_verdict)

        model_config = admin_store.get_model_config()
        generated = generate_sql(
            question=request.question,
            dialect=engine.dialect.name,
            schema_catalog=schema_catalog,
            schema_relationships=_schema_relationships(engine, schema_catalog),
            examples=examples,
            linkable_schema=linkable_schema,
            provider_factory=provider_factory,
            model=model_config.model or default_model(model_config.provider),
            max_rows=_effective_max_rows(),
            trace=trace,
            value_normalization_map=value_normalization_map,
            conversation_history=[
                {
                    "question": turn.question,
                    "sql": turn.sql,
                    "nl_summary": turn.nl_summary or "",
                }
                for turn in request.conversation_history
            ],
            failed_sql=failed_sql,
            execution_error_feedback=execution_error_feedback,
        )
        output_verdict = _evaluate_traced_guardrail(
            stage="output_safety",
            trace=trace,
            evaluator=guardrail.evaluate_output,
            question=request.question,
            sql=generated.sql,
            explanation=generated.explanation,
            schema_catalog=schema_catalog,
        )
        if output_verdict.verdict == "block":
            raise GuardrailBlockedError("output", output_verdict)
        if trace is not None:
            trace.record_event("request_completed", outcome="generated_sql", example_ids=generated.examples_used)
        generated.transpiled_sql = _transpile(
            generated.sql, engine.dialect.name, model_config.transpile_dialect
        )
        return generated
    except GuardrailBlockedError as error:
        logger.warning(
            "openevals_guardrail_block stage=%s reason_code=%s",
            error.stage,
            error.reason_code,
        )
        status_code, detail = _guardrail_block_http_response(error)
        raise HTTPException(
            status_code=status_code,
            detail=detail,
        ) from None
    except GuardrailSetupError:
        if trace is not None:
            trace.record_event("guardrail_decision", stage="setup", verdict="block", reason_code="unavailable")
        logger.error("openevals_guardrail_unavailable")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Request could not be completed safely.",
        ) from None
    except InputOutOfScopeError:
        if trace is not None:
            trace.record_event(
                "guardrail_decision",
                stage="input_scope",
                verdict="block",
                reason_code="deterministic_scope_check",
            )
        logger.warning("openevals_guardrail_block stage=input reason_code=deterministic_scope_check")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Request could not be completed safely.",
        ) from None
    except UnsupportedQuestionError:
        if trace is not None:
            trace.record_event(
                "guardrail_decision",
                stage="sql_intent",
                verdict="block",
                reason_code="unsupported_sql_intent",
            )
        logger.warning("openevals_guardrail_block stage=input reason_code=unsupported_sql_intent")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Request could not be completed safely.",
        ) from None
    except AmbiguousQuestionError as error:
        if trace is not None:
            trace.record_event(
                "request_decision",
                stage="schema_linking",
                verdict="clarify",
                reason_code="ambiguous_schema_term",
                ambiguities=error.ambiguities,
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": str(error), "ambiguities": error.ambiguities},
        ) from None
    except SQLAlchemyError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The configured database schema could not be inspected.",
        ) from None
    except (OSError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The curated example library could not be loaded.",
        ) from None
    except InvalidGenerationResponseError as error:
        if trace is not None:
            trace.record_event(
                "guardrail_decision",
                stage="sql_validation",
                verdict="block",
                reason_code="sql_validation_failed",
            )
        logger.warning(
            "openevals_guardrail_block stage=output reason_code=sql_validation_failed detail=%s",
            str(error),
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=_user_safe_sql_validation_message(error),
        ) from None
    except ProviderUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(error),
        ) from None
    finally:
        close_guardrail = getattr(guardrail, "close", None)
        if callable(close_guardrail):
            try:
                close_guardrail()
            except Exception:
                if trace is not None:
                    trace.record_event("guardrail_client_close_failed", reason_code="cleanup_error")


@router.post(
    "/query/generate",
    response_model=SQLGenerationResponse,
    tags=["query"],
)
def generate_query_sql(
    request: QueryRequest,
    engine: Engine = Depends(get_database_engine),
    provider_factory: Callable[[], CompletionProvider] = Depends(get_sql_completion_provider),
    guardrail_factory: Callable[[], SafetyGuardrail] = Depends(get_safety_guardrail),
) -> SQLGenerationResponse:
    trace = get_current_trace()
    trace_id = str(trace.trace_id) if trace else None
    try:
        generated = _generate_sql_or_raise(
            request,
            engine=engine,
            provider_factory=provider_factory,
            guardrail_factory=guardrail_factory,
            trace=trace,
        )
    except HTTPException as error:
        admin_store.record_query_log(
            trace_id=trace_id,
            question=request.question,
            status=_log_status_for_http_error(error.status_code),
            error_reason=str(error.detail)[:500],
        )
        raise
    admin_store.record_query_log(
        trace_id=trace_id,
        question=request.question,
        status="generated",
        sql=generated.sql,
    )
    return generated


@router.post(
    "/query",
    response_model=QueryResponse,
    tags=["query"],
)
def submit_query(
    request: QueryRequest,
    engine: Engine = Depends(get_database_engine),
    provider_factory: Callable[[], CompletionProvider] = Depends(get_sql_completion_provider),
    guardrail_factory: Callable[[], SafetyGuardrail] = Depends(get_safety_guardrail),
) -> QueryResponse:
    trace = get_current_trace()
    trace_id = str(trace.trace_id) if trace else None
    if semantic_cache.is_cacheable_question(request.question):
        cached_response = semantic_cache.get(request.question)
        if cached_response is not None:
            try:
                if not request.include_explanation:
                    cached_response["explanation"] = None
                return QueryResponse(**cached_response)
            except Exception:
                if trace is not None:
                    trace.record_event("semantic_cache_invalid_entry", reason_code="schema_mismatch")
    try:
        generated = _generate_sql_or_raise(
            request,
            engine=engine,
            provider_factory=provider_factory,
            guardrail_factory=guardrail_factory,
            trace=trace,
        )
    except HTTPException as error:
        admin_store.record_query_log(
            trace_id=trace_id,
            question=request.question,
            status=_log_status_for_http_error(error.status_code),
            error_reason=str(error.detail)[:500],
        )
        raise
    execution_sql = generated.sql
    execution = None
    with trace.span(
        "sql_execution", inputs={"sql": execution_sql}
    ) if trace else nullcontext() as span:
        try:
            execution = execute_validated_sql(
                engine,
                execution_sql,
                max_rows=_effective_max_rows(),
                timeout_seconds=get_query_timeout_seconds(),
            )
        except QueryExecutionTimeoutError as error:
            if span:
                span.set_outputs(verdict="error", reason_code="execution_timeout")
            admin_store.record_query_log(
                trace_id=trace_id,
                question=request.question,
                status="execution_timeout",
                sql=generated.sql,
                error_reason=str(error)[:500],
            )
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail=str(error),
            ) from None
        except QueryExecutionError as error:
            if trace is not None:
                trace.record_event(
                    "sql_execution_retry",
                    reason_code="execution_failed",
                    error=str(error)[:500],
                )
            regenerated = _generate_sql_or_raise(
                request,
                engine=engine,
                provider_factory=provider_factory,
                guardrail_factory=guardrail_factory,
                trace=trace,
                failed_sql=execution_sql,
                execution_error_feedback=str(error),
            )
            execution_sql = regenerated.sql
            generated = regenerated
            try:
                execution = execute_validated_sql(
                    engine,
                    execution_sql,
                    max_rows=_effective_max_rows(),
                    timeout_seconds=get_query_timeout_seconds(),
                )
            except QueryExecutionError:
                if span:
                    span.set_outputs(verdict="error", reason_code="execution_failed")
                admin_store.record_query_log(
                    trace_id=trace_id,
                    question=request.question,
                    status="execution_error",
                    sql=execution_sql,
                    error_reason="execution_failed_after_retry",
                )
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="The validated query could not be executed against the configured database.",
                ) from None
        if span:
            span.set_outputs(
                row_count=execution.row_count,
                latency_ms=execution.latency_ms,
                columns=execution.columns,
            )

    table, nl_summary = format_query_result(execution, question=request.question)
    if trace is not None:
        trace.record_event(
            "request_completed",
            outcome="executed_sql",
            example_ids=generated.examples_used,
            row_count=execution.row_count,
        )
    response = QueryResponse(
        status="ok",
        question=request.question,
        sql=generated.sql,
        explanation=generated.explanation if request.include_explanation else None,
        query_type=generated.query_type,
        examples_used=generated.examples_used,
        columns=execution.columns,
        rows=table,
        row_count=execution.row_count,
        latency_ms=execution.latency_ms,
        nl_summary=nl_summary,
        transpiled_sql=generated.transpiled_sql,
        transpile_dialect=admin_store.get_model_config().transpile_dialect if generated.transpiled_sql else None,
    )
    if semantic_cache.is_cacheable_question(request.question):
        semantic_cache.put(request.question, response.model_dump())
    admin_store.record_query_log(
        trace_id=trace_id,
        question=request.question,
        status="success",
        sql=response.sql,
        row_count=execution.row_count,
        latency_ms=execution.latency_ms,
    )
    return response