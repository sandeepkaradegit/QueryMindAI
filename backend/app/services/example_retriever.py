from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from app.schemas.examples import ExampleRetrievalRequest, QuestionSqlExample, RetrievedExample
from app import admin_store
from app.services import vector_index

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_EXAMPLES_PATH = PROJECT_ROOT / "examples" / "question_sql_examples.json"


_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")
_STOP_WORDS = {
    "a", "an", "and", "are", "by", "for", "from", "how", "in", "is", "of", "on", "or", "the", "there", "to", "what", "with",
}


def _normalize_token(token: str) -> str:
    if len(token) > 4 and token.endswith("ies"):
        return f"{token[:-3]}y"
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _features(text: str) -> Counter[str]:
    tokens = [
        _normalize_token(token)
        for token in _TOKEN_PATTERN.findall(text.lower())
        if token not in _STOP_WORDS
    ]
    features = Counter(tokens)
    features.update(f"{left}_{right}" for left, right in zip(tokens, tokens[1:]))
    return features


def _tfidf_vectors(
    query_features: Counter[str],
    document_features: Sequence[Counter[str]],
) -> tuple[dict[str, float], list[dict[str, float]]]:
    document_frequency: Counter[str] = Counter()
    for features in document_features:
        document_frequency.update(features.keys())

    document_count = len(document_features)

    def weighted(features: Counter[str]) -> dict[str, float]:
        vector = {
            feature: count * (math.log((document_count + 1) / (document_frequency[feature] + 1)) + 1.0)
            for feature, count in features.items()
        }
        magnitude = math.sqrt(sum(weight * weight for weight in vector.values()))
        return {feature: weight / magnitude for feature, weight in vector.items()} if magnitude else {}

    return weighted(query_features), [weighted(features) for features in document_features]


def retrieve_examples(
    question: str,
    examples: Sequence[QuestionSqlExample],
    top_k: int = 3,
) -> list[RetrievedExample]:
    if not examples or top_k <= 0:
        return []

    vector_hits = vector_index.search(question, [(example.id, example.question) for example in examples], top_k)
    if vector_hits is not None:
        by_id = {example.id: example for example in examples}
        return [
            RetrievedExample(**by_id[example_id].model_dump(), similarity=round(score, 6))
            for example_id, score in vector_hits
        ]

    return _retrieve_lexical(question, examples, top_k)


def _retrieve_lexical(
    question: str,
    examples: Sequence[QuestionSqlExample],
    top_k: int,
) -> list[RetrievedExample]:
    document_features = [_features(example.question) for example in examples]
    query_features, document_vectors = _tfidf_vectors(_features(question), document_features)
    ranked: list[tuple[float, QuestionSqlExample]] = []

    for example, document_vector in zip(examples, document_vectors):
        similarity = sum(
            query_weight * document_vector.get(feature, 0.0)
            for feature, query_weight in query_features.items()
        )
        if similarity > 0:
            ranked.append((similarity, example))

    ranked.sort(key=lambda item: (-item[0], item[1].id))
    return [
        RetrievedExample(**example.model_dump(), similarity=round(similarity, 6))
        for similarity, example in ranked[:top_k]
    ]


@dataclass(frozen=True)
class RerankResult:
    example: RetrievedExample
    retrieval_score: float
    rerank_score: float


def rerank_examples(
    question: str,
    candidates: Sequence[RetrievedExample],
    top_k: int = 3,
) -> tuple[list[RetrievedExample], list[RerankResult]]:
    query_terms = set(_features(question))
    reranked: list[RerankResult] = []

    for candidate in candidates:
        candidate_terms = set(_features(candidate.question))
        coverage = len(query_terms & candidate_terms) / len(query_terms) if query_terms else 0.0
        rerank_score = 0.65 * candidate.similarity + 0.35 * coverage
        reranked.append(
            RerankResult(
                example=candidate,
                retrieval_score=candidate.similarity,
                rerank_score=round(rerank_score, 6),
            )
        )

    reranked.sort(key=lambda result: (-result.rerank_score, result.example.id))
    return (
        [result.example for result in reranked[:top_k]],
        reranked,
    )


def retrieve_and_rerank_examples(
    question: str,
    examples: Sequence[QuestionSqlExample],
    top_k: int = 3,
    candidate_k: int = 10,
) -> tuple[list[RetrievedExample], list[RerankResult]]:
    candidates = retrieve_examples(question, examples, top_k=max(top_k, candidate_k))
    return rerank_examples(question, candidates, top_k)


def load_example_library(path: Path = DEFAULT_EXAMPLES_PATH) -> list[QuestionSqlExample]:
    if path.is_file():
        raw_examples = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw_examples, list):
            raise ValueError("The example library must contain a JSON array.")
        validated = [QuestionSqlExample.model_validate(example) for example in raw_examples]
        admin_store.seed_examples_if_empty(
            [
                {"id": example.id, "question": example.question, "sql": example.sql, "query_type": example.query_type.value}
                for example in validated
            ]
        )
    return [
        QuestionSqlExample(id=record.id, question=record.question, sql=record.sql, query_type=record.query_type)
        for record in admin_store.list_examples()
    ]