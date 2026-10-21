from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Literal

import sqlglot
from pydantic import BaseModel, ConfigDict, model_validator
from sqlalchemy import inspect


ROOT = Path(__file__).resolve().parents[1]
BACKEND_PATH = ROOT / "backend"
DATASET_PATH = ROOT / "evaluations" / "sql_generation_v1.json"
if str(BACKEND_PATH) not in sys.path:
    sys.path.insert(0, str(BACKEND_PATH))

from app.database import DEFAULT_DB_PATH, create_database_engine
from app.schemas.classification import QueryType
from app.services.example_retriever import load_example_library, retrieve_and_rerank_examples
from app.services.openevals_guardrails import GuardrailBlockedError
from app.services.sql_generator import (
    AmbiguousQuestionError,
    UnsupportedQuestionError,
    generate_sql,
)
from app.services.sql_guardrails import InputOutOfScopeError


class EvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    question: str
    expected_action: Literal["answer", "refuse", "clarify"]
    expected_answer: str | None = None
    expected_query_type: QueryType | None = None
    expected_error: Literal[
        "unsupported_question",
        "input_out_of_scope",
        "ambiguous_question",
    ] | None = None
    relevant_example_ids: list[str]
    tags: list[str]

    @model_validator(mode="after")
    def validate_expected_outcome(self) -> EvaluationCase:
        if self.expected_action == "answer":
            if not self.expected_answer or self.expected_query_type is None or self.expected_error:
                raise ValueError("Answer cases require SQL and a query type, and cannot define an error.")
            if not self.relevant_example_ids:
                raise ValueError("Answer cases require at least one labeled relevant example.")
        elif self.expected_action == "refuse":
            if self.expected_answer or self.expected_query_type is not None or self.expected_error is None:
                raise ValueError("Refusal cases require only an expected error category.")
        elif self.expected_action == "clarify":
            if self.expected_answer or self.expected_query_type is not None:
                raise ValueError("Clarification cases cannot define a SQL answer or query type.")
            if self.expected_error != "ambiguous_question":
                raise ValueError("Clarification cases must expect an ambiguous-question error.")
        return self


class EvaluationDataset(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    dataset_version: str
    description: str
    cases: list[EvaluationCase]

    @model_validator(mode="after")
    def validate_case_ids(self) -> EvaluationDataset:
        case_ids = [case.id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("Evaluation case IDs must be unique.")
        if len(self.cases) < 15:
            raise ValueError("The evaluation dataset must contain at least 15 cases.")
        if sum(case.expected_action == "answer" for case in self.cases) < 25:
            raise ValueError("The evaluation dataset must contain at least 25 answer cases with ground-truth SQL.")
        if not any("adversarial" in case.tags for case in self.cases):
            raise ValueError("The evaluation dataset must include adversarial cases.")
        return self


class FixtureProvider:
    def __init__(self, sql: str) -> None:
        self.sql = sql
        self.calls = 0

    def complete(self, messages: list[dict[str, str]], model: str) -> str:
        self.calls += 1
        return json.dumps(
            {
                "sql": self.sql,
                "explanation": "Deterministic evaluation fixture; no live model was called.",
            }
        )


class CountingProvider:
    """Wraps a live provider so refusal cases can assert the model was never called."""

    def __init__(self, inner) -> None:
        self._inner = inner
        self.calls = 0

    def complete(self, messages: list[dict[str, str]], model: str) -> str:
        self.calls += 1
        return self._inner.complete(messages, model)


def _run_rows(engine, sql: str) -> list[tuple]:
    with engine.connect() as connection:
        rows = connection.exec_driver_sql(sql).fetchall()
    return [
        tuple(round(value, 6) if isinstance(value, float) else value for value in row)
        for row in rows
    ]


def _execution_match(engine, generated_sql: str, reference_sql: str) -> bool:
    try:
        generated_rows = _run_rows(engine, generated_sql)
        reference_rows = _run_rows(engine, reference_sql)
    except Exception:
        return False
    if " order by " in f" {reference_sql.lower()} ":
        return generated_rows == reference_rows
    return sorted(generated_rows, key=repr) == sorted(reference_rows, key=repr)


def _sql_exact_match(generated_sql: str, reference_sql: str, dialect: str) -> bool:
    try:
        generated = sqlglot.parse_one(generated_sql, read=dialect)
        reference = sqlglot.parse_one(reference_sql, read=dialect)
        if generated.sql(dialect=dialect) == reference.sql(dialect=dialect):
            return True
        edits = sqlglot.diff(generated, reference)
        return all(isinstance(edit, sqlglot.diff.Keep) for edit in edits)
    except Exception:
        return False


def _load_dataset() -> tuple[EvaluationDataset, str]:
    raw_dataset = DATASET_PATH.read_bytes()
    dataset = EvaluationDataset.model_validate_json(raw_dataset)
    return dataset, hashlib.sha256(raw_dataset).hexdigest()


def _schema_catalog(engine) -> dict[str, list[str]]:
    inspector = inspect(engine)
    schema_name = inspector.default_schema_name
    names = set(inspector.get_table_names(schema=schema_name))
    names.update(inspector.get_view_names(schema=schema_name))
    return {
        name: [column["name"] for column in inspector.get_columns(name, schema=schema_name)]
        for name in sorted(names)
    }


def _canonical_sql(sql: str, dialect: str) -> str:
    statements = [statement for statement in sqlglot.parse(sql, read=dialect) if statement]
    if len(statements) != 1:
        raise ValueError("Expected a single SQL statement.")
    return statements[0].sql(dialect=dialect, pretty=False)


def _error_category(error: Exception) -> str | None:
    if isinstance(error, AmbiguousQuestionError):
        return "ambiguous_question"
    if isinstance(error, UnsupportedQuestionError):
        return "unsupported_question"
    if isinstance(error, InputOutOfScopeError):
        return "input_out_of_scope"
    if isinstance(error, GuardrailBlockedError):
        return f"guardrail_blocked:{error.stage}:{error.reason_code}"
    return None


def _evaluate_case(
    case: EvaluationCase,
    *,
    dialect: str,
    schema_catalog: dict[str, list[str]],
    examples,
    engine,
    live_provider=None,
    model: str = "deterministic-evaluation-fixture",
) -> dict[str, object]:
    fixture_sql = case.expected_answer or "SELECT 1"
    provider = CountingProvider(live_provider) if live_provider else FixtureProvider(fixture_sql)
    retrieved, _ = retrieve_and_rerank_examples(case.question, examples, top_k=3, candidate_k=10)
    retrieved_ids = [example.id for example in retrieved]
    checks: dict[str, bool] = {}
    actual_action = "error"
    actual_error: str | None = None
    generated_sql: str | None = None
    actual_query_type: str | None = None
    exact_match = False
    execution_match = False

    try:
        response = generate_sql(
            question=case.question,
            dialect=dialect,
            schema_catalog=schema_catalog,
            examples=examples,
            provider_factory=lambda: provider,
            model=model,
            max_rows=100,
        )
    except Exception as error:
        actual_error = _error_category(error) or type(error).__name__
        actual_action = "clarify" if actual_error == "ambiguous_question" else "refuse"
        checks["expected_action"] = actual_action == case.expected_action
        checks["expected_error"] = actual_error == case.expected_error
        checks["provider_not_called"] = provider.calls == 0
    else:
        actual_action = "answer"
        generated_sql = response.sql
        actual_query_type = response.query_type.value
        if case.expected_answer is not None:
            exact_match = _sql_exact_match(response.sql, case.expected_answer, dialect)
            execution_match = _execution_match(engine, response.sql, case.expected_answer)
        checks["execution_match"] = execution_match
        if live_provider is None:
            checks["sql_matches_reference"] = exact_match
            checks["provider_called_once"] = provider.calls == 1
        checks["query_type_matches"] = actual_query_type == (
            case.expected_query_type.value if case.expected_query_type else None
        )
        checks["retrieved_examples_match_response"] = response.examples_used == retrieved_ids

    passed = bool(checks) and all(checks.values())
    result: dict[str, object] = {
        "id": case.id,
        "tags": case.tags,
        "expected_action": case.expected_action,
        "actual_action": actual_action,
        "expected_error": case.expected_error,
        "actual_error": actual_error,
        "expected_query_type": case.expected_query_type.value if case.expected_query_type else None,
        "actual_query_type": actual_query_type,
        "generated_sql": generated_sql,
        "exact_match": exact_match,
        "execution_match": execution_match,
        "retrieved_example_ids": retrieved_ids,
        "relevant_example_ids": case.relevant_example_ids,
        "checks": checks,
        "passed": passed,
    }
    if case.expected_action == "answer":
        relevant = set(case.relevant_example_ids)
        retrieved_set = set(retrieved_ids)
        hits = len(relevant & retrieved_set)
        result["context_precision_at_3"] = hits / len(retrieved_ids) if retrieved_ids else 0.0
        result["context_recall_at_3"] = hits / len(relevant) if relevant else 0.0
    return result


def run_evaluation(live: bool = False) -> dict[str, object]:
    dataset, dataset_sha256 = _load_dataset()
    if not DEFAULT_DB_PATH.is_file():
        raise FileNotFoundError(f"Seeded demo database is missing: {DEFAULT_DB_PATH}")

    live_provider = None
    model = "deterministic-evaluation-fixture"
    if live:
        from app.config import get_groq_api_key, get_groq_model
        from app.services.sql_generator import GroqCompletionProvider

        api_key = get_groq_api_key()
        if api_key is None:
            raise RuntimeError("Live evaluation requires GROQ_API_KEY in the local .env file.")
        live_provider = GroqCompletionProvider(api_key, temperature=0.0)
        model = get_groq_model()

    database_url = f"sqlite:///{DEFAULT_DB_PATH.as_posix()}"
    engine = create_database_engine(database_url)
    try:
        dialect = engine.dialect.name
        schema_catalog = _schema_catalog(engine)
        examples = load_example_library()
        case_results = [
            _evaluate_case(
                case,
                dialect=dialect,
                schema_catalog=schema_catalog,
                examples=examples,
                engine=engine,
                live_provider=live_provider,
                model=model,
            )
            for case in dataset.cases
        ]
    finally:
        engine.dispose()

    action_counts = Counter(str(result["actual_action"]) for result in case_results)
    answer_results = [result for result in case_results if result["expected_action"] == "answer"]
    context_precision = [float(result["context_precision_at_3"]) for result in answer_results]
    context_recall = [float(result["context_recall_at_3"]) for result in answer_results]
    passed_count = sum(bool(result["passed"]) for result in case_results)
    total_cases = len(case_results)

    def _rate(results: list[dict[str, object]], key: str) -> float:
        return sum(bool(result[key]) for result in results) / len(results) if results else 0.0

    by_query_type: dict[str, dict[str, object]] = {}
    for query_type in sorted({str(result["expected_query_type"]) for result in answer_results}):
        group = [result for result in answer_results if result["expected_query_type"] == query_type]
        by_query_type[query_type] = {
            "cases": len(group),
            "execution_accuracy": _rate(group, "execution_match"),
            "exact_match_rate": _rate(group, "exact_match"),
        }

    return {
        "evaluation_version": "2.0",
        "evaluation_mode": "live_llm" if live else "offline_fixture_pipeline_validation",
        "live_llm_calls": len(answer_results) if live else 0,
        "model": model,
        "dataset_version": dataset.dataset_version,
        "dataset_sha256": dataset_sha256,
        "database": "seeded_demo_sqlite",
        "dialect": dialect,
        "retrieval_top_k": 3,
        "summary": {
            "total_cases": total_cases,
            "answer_cases": len(answer_results),
            "passed_cases": passed_count,
            "failed_cases": total_cases - passed_count,
            "case_pass_rate": passed_count / total_cases if total_cases else 0.0,
            "execution_accuracy": _rate(answer_results, "execution_match"),
            "exact_match_rate": _rate(answer_results, "exact_match"),
            "by_query_type": by_query_type,
            "actual_action_counts": dict(sorted(action_counts.items())),
            "macro_context_precision_at_3": (
                sum(context_precision) / len(context_precision) if context_precision else 0.0
            ),
            "macro_context_recall_at_3": (
                sum(context_recall) / len(context_recall) if context_recall else 0.0
            ),
        },
        "cases": case_results,
    }


def render_markdown_report(report: dict[str, object]) -> str:
    summary = report["summary"]
    lines = [
        "# QueryMind SQL Evaluation Report",
        "",
        f"- Mode: `{report['evaluation_mode']}`",
        f"- Model: `{report['model']}`",
        f"- Dataset: v{report['dataset_version']} (sha256 `{str(report['dataset_sha256'])[:12]}`)",
        f"- Dialect: `{report['dialect']}`",
        f"- Total cases: {summary['total_cases']} ({summary['answer_cases']} answer cases with ground-truth SQL)",
        f"- Case pass rate: {summary['case_pass_rate']:.1%}",
        f"- **Execution accuracy:** {summary['execution_accuracy']:.1%}",
        f"- **Exact match rate (sqlglot):** {summary['exact_match_rate']:.1%}",
        f"- Context precision@3 / recall@3: {summary['macro_context_precision_at_3']:.2f} / {summary['macro_context_recall_at_3']:.2f}",
        "",
        "## Per-query-type breakdown",
        "",
        "| Query type | Cases | Execution accuracy | Exact match |",
        "|---|---|---|---|",
    ]
    for query_type, stats in summary["by_query_type"].items():
        lines.append(
            f"| {query_type} | {stats['cases']} | {stats['execution_accuracy']:.1%} | {stats['exact_match_rate']:.1%} |"
        )
    lines += ["", "## Failed cases", ""]
    failed = [case for case in report["cases"] if not case["passed"]]
    if failed:
        for case in failed:
            lines.append(f"- `{case['id']}`: checks {case['checks']}")
    else:
        lines.append("None.")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run QueryMind SQL-pipeline evaluation.")
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional path to write the JSON report.",
    )
    parser.add_argument("--report", type=Path, help="Optional path to write a Markdown report.")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Call the configured Groq model instead of the deterministic fixture provider.",
    )
    arguments = parser.parse_args()

    report = run_evaluation(live=arguments.live)
    rendered_report = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(rendered_report, encoding="utf-8")
    if arguments.report:
        arguments.report.parent.mkdir(parents=True, exist_ok=True)
        arguments.report.write_text(render_markdown_report(report), encoding="utf-8")
    print(rendered_report, end="")
    return 0 if report["summary"]["failed_cases"] == 0 or arguments.live else 1


if __name__ == "__main__":
    raise SystemExit(main())