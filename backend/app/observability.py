from __future__ import annotations

import contextvars
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from langsmith import Client
from langsmith.run_trees import RunTree


logger = logging.getLogger("querymind.observability")
logger.setLevel(logging.INFO)
logger.propagate = False
if not logger.handlers:
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(stream_handler)
_current_trace: contextvars.ContextVar[RequestTrace | None] = contextvars.ContextVar(
    "querymind_current_trace",
    default=None,
)


def _tracing_configuration() -> tuple[bool, str | None, str]:
    api_key = os.environ.get("LANGSMITH_API_KEY", "").strip() or None
    explicitly_enabled = os.environ.get("LANGSMITH_TRACING", "").strip().lower()
    enabled = explicitly_enabled in {"true", "1", "yes"}
    if explicitly_enabled not in {"true", "1", "yes", "false", "0", "no"}:
        enabled = api_key is not None
    project = os.environ.get("LANGSMITH_PROJECT", "querymind-ai").strip() or "querymind-ai"
    return enabled and api_key is not None, api_key, project


def _log_event(event: dict[str, Any]) -> None:
    logger.info(json.dumps(event, ensure_ascii=True, sort_keys=True, default=str))


class TraceSpan:
    def __init__(
        self,
        trace: RequestTrace,
        *,
        name: str,
        run_type: str,
        inputs: dict[str, Any],
        parent_run_id: UUID,
    ) -> None:
        self.trace = trace
        self.name = name
        self.inputs = inputs
        self.outputs: dict[str, Any] = {}
        self.run_id = uuid4()
        self.started_at = datetime.now(timezone.utc)
        self.started_counter = time.perf_counter()
        self.error: str | None = None
        self.run_type = run_type
        self.trace._create_run(
            name=name,
            run_type=run_type,
            inputs=inputs,
            run_id=self.run_id,
            parent_run_id=parent_run_id,
        )
        self.trace.record_event("span_started", span=name, span_id=str(self.run_id), inputs=inputs)

    def set_outputs(self, **outputs: Any) -> None:
        self.outputs.update(outputs)

    def __enter__(self) -> TraceSpan:
        return self

    def __exit__(self, exception_type, exception, traceback) -> bool:
        elapsed_ms = round((time.perf_counter() - self.started_counter) * 1000, 3)
        if exception is not None:
            self.error = type(exception).__name__
        outputs = {**self.outputs, "latency_ms": elapsed_ms}
        self.trace._finish_run(self.run_id, outputs=outputs, error=self.error)
        self.trace.record_event(
            "span_finished",
            span=self.name,
            span_id=str(self.run_id),
            latency_ms=elapsed_ms,
            outputs=outputs,
            error=self.error,
        )
        return False


class RequestTrace:
    def __init__(self, method: str, path: str) -> None:
        self.trace_id = uuid4()
        self.root_run_id = self.trace_id
        self.started_at = datetime.now(timezone.utc)
        self.started_counter = time.perf_counter()
        self.inputs: dict[str, Any] = {"method": method, "path": path}
        self.token_usage: dict[str, int | None] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        self.usage_calls = 0
        self.unreported_usage_calls = 0
        self._client: Client | None = None
        self._project_name: str | None = None
        self._run_trees: dict[UUID, RunTree] = {}

        enabled, api_key, project_name = _tracing_configuration()
        if enabled and api_key:
            try:
                self._client = Client(api_key=api_key)
                self._project_name = project_name
            except Exception as error:
                self.record_event(
                    "trace_export_unavailable",
                    reason_code="client_initialization_failed",
                    error_type=type(error).__name__,
                )

        self._create_run(
            name="querymind_request",
            run_type="chain",
            inputs=self.inputs,
            run_id=self.root_run_id,
            parent_run_id=None,
        )
        self.record_event(
            "request_started",
            langsmith_enabled=self._client is not None,
            method=method,
            path=path,
        )

    def update_inputs(self, **inputs: Any) -> None:
        self.inputs.update(inputs)

    def span(
        self,
        name: str,
        *,
        inputs: dict[str, Any] | None = None,
        run_type: str = "chain",
    ) -> TraceSpan:
        return TraceSpan(
            self,
            name=name,
            run_type=run_type,
            inputs=inputs or {},
            parent_run_id=self.root_run_id,
        )

    def record_event(self, event_name: str, **values: Any) -> None:
        _log_event(
            {
                "event": event_name,
                "trace_id": str(self.trace_id),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                **values,
            }
        )

    def add_token_usage(self, usage: dict[str, int | None] | None) -> None:
        self.usage_calls += 1
        if usage is None:
            self.unreported_usage_calls += 1
            return
        usage_incomplete = False
        for key in self.token_usage:
            value = usage.get(key)
            if value is None:
                usage_incomplete = True
                continue
            self.token_usage[key] = int(self.token_usage[key] or 0) + int(value)
        if usage_incomplete:
            self.unreported_usage_calls += 1

    def close(self, *, status_code: int, error: str | None = None) -> None:
        elapsed_ms = round((time.perf_counter() - self.started_counter) * 1000, 3)
        token_usage: dict[str, int | None] = dict(self.token_usage)
        complete_token_usage = self.unreported_usage_calls == 0
        outputs = {
            "status_code": status_code,
            "latency_ms": elapsed_ms,
            "token_usage": token_usage,
            "token_usage_complete": complete_token_usage,
            "unreported_usage_calls": self.unreported_usage_calls,
        }
        self._finish_run(self.root_run_id, outputs=outputs, error=error, inputs=self.inputs)
        self.record_event(
            "request_metrics",
            status_code=status_code,
            latency_ms=elapsed_ms,
            token_usage=token_usage,
            token_usage_complete=complete_token_usage,
            unreported_usage_calls=self.unreported_usage_calls,
        )
        if self._client is not None:
            try:
                self._client.flush(timeout=2.0)
            except Exception:
                self.record_event("trace_export_unavailable", reason_code="flush_failed")

    def _create_run(
        self,
        *,
        name: str,
        run_type: str,
        inputs: dict[str, Any],
        run_id: UUID,
        parent_run_id: UUID | None,
    ) -> None:
        if self._client is None:
            return
        try:
            started_at = datetime.now(timezone.utc)
            if parent_run_id is None:
                run_tree = RunTree(
                    id=run_id,
                    name=name,
                    inputs=inputs,
                    run_type=run_type,
                    project_name=self._project_name,
                    start_time=started_at,
                    extra={"metadata": {"service": "querymind-ai"}},
                    ls_client=self._client,
                )
            else:
                parent_run = self._run_trees[parent_run_id]
                run_tree = parent_run.create_child(
                    name=name,
                    run_type=run_type,
                    run_id=run_id,
                    inputs=inputs,
                    start_time=started_at,
                    extra={"metadata": {"service": "querymind-ai"}},
                )
            self._run_trees[run_id] = run_tree
            run_tree.post()
        except Exception as error:
            self.record_event(
                "trace_export_unavailable",
                reason_code="run_creation_failed",
                span=name,
                error_type=type(error).__name__,
            )

    def _finish_run(
        self,
        run_id: UUID,
        *,
        outputs: dict[str, Any],
        error: str | None,
        inputs: dict[str, Any] | None = None,
    ) -> None:
        if self._client is None:
            return
        try:
            self._client.update_run(
                run_id,
                inputs=inputs,
                outputs=outputs,
                error=error,
                end_time=datetime.now(timezone.utc),
            )
        except Exception as error:
            self.record_event(
                "trace_export_unavailable",
                reason_code="run_update_failed",
                error_type=type(error).__name__,
            )


def get_current_trace() -> RequestTrace | None:
    return _current_trace.get()


def set_current_trace(trace: RequestTrace) -> contextvars.Token[RequestTrace | None]:
    return _current_trace.set(trace)


def reset_current_trace(token: contextvars.Token[RequestTrace | None]) -> None:
    _current_trace.reset(token)