from __future__ import annotations

import json
import logging
import os
import re
import threading
from collections.abc import Mapping

logger = logging.getLogger(__name__)

DEFAULT_SIMILARITY = 0.92
DEFAULT_MAX_ITEMS = 500

_lock = threading.Lock()
_items: list[tuple[str, str]] = []
_index = None
_vectors_ready = False
_unavailable = False
_TEMPORAL_QUERY_PATTERN = re.compile(
    r"\b(?:today|yesterday|tomorrow|now|current date|current time|"
    r"this (?:day|week|month|quarter|year)|"
    r"last (?:day|week|month|quarter|year)|"
    r"past (?:day|week|month|quarter|year)|"
    r"previous (?:day|week|month|quarter|year))\b"
    r"|\b\d{4}-\d{2}-\d{2}\b"
    r"|\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b",
    re.IGNORECASE,
)
_TOKEN_PATTERN = re.compile(r"[a-z0-9_]+", re.IGNORECASE)
_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "by", "for", "from", "get", "give", "how", "i", "in", "is",
    "it", "its", "list", "me", "of", "on", "or", "show", "the", "their", "them", "to", "under", "what",
    "which", "with",
}


def _threshold() -> float:
    try:
        return float(os.getenv("QUERYMIND_SEMANTIC_CACHE_MIN_SIMILARITY", str(DEFAULT_SIMILARITY)))
    except ValueError:
        return DEFAULT_SIMILARITY


def _enabled() -> bool:
    return os.getenv("QUERYMIND_SEMANTIC_CACHE_ENABLED", "1").strip().lower() not in {"0", "false", "off", "no"}


def _max_items() -> int:
    try:
        configured = int(os.getenv("QUERYMIND_SEMANTIC_CACHE_MAX_ITEMS", str(DEFAULT_MAX_ITEMS)))
    except ValueError:
        return DEFAULT_MAX_ITEMS
    return min(max(configured, 1), 5000)


def is_cacheable_question(question: str) -> bool:
    trimmed = question.strip()
    if not trimmed:
        return False
    return _TEMPORAL_QUERY_PATTERN.search(trimmed) is None


def _salient_tokens(text: str) -> set[str]:
    tokens = {token.lower() for token in _TOKEN_PATTERN.findall(text)}
    return {token for token in tokens if len(token) > 2 and token not in _STOP_WORDS}


def _embed_texts(texts: list[str]):
    import faiss
    import numpy as np
    from fastembed import TextEmbedding

    model = os.getenv("QUERYMIND_EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
    embedder = TextEmbedding(model)
    vectors = np.array(list(embedder.embed(texts)), dtype="float32")
    faiss.normalize_L2(vectors)
    return vectors


def _rebuild_index() -> None:
    global _index, _vectors_ready
    import faiss

    if not _items:
        _index = None
        _vectors_ready = False
        return
    vectors = _embed_texts([question for question, _ in _items])
    index = faiss.IndexFlatIP(vectors.shape[1])
    index.add(vectors)
    _index = index
    _vectors_ready = True


def put(question: str, response: Mapping[str, object]) -> None:
    global _unavailable
    if not _enabled() or not question.strip():
        return
    payload = json.dumps(response, ensure_ascii=True)
    with _lock:
        _items.append((question, payload))
        cap = _max_items()
        if len(_items) > cap:
            del _items[:-cap]
        try:
            _rebuild_index()
            _unavailable = False
        except Exception:
            logger.warning("semantic_cache_index_rebuild_failed", exc_info=True)
            _unavailable = True


def get(question: str) -> dict[str, object] | None:
    if not _enabled() or _unavailable or not question.strip():
        return None
    with _lock:
        if not _items or not _vectors_ready or _index is None:
            return None
        try:
            scores, positions = _index.search(_embed_texts([question]), 1)
        except Exception:
            logger.warning("semantic_cache_lookup_failed", exc_info=True)
            return None
        score = float(scores[0][0])
        position = int(positions[0][0])
        if position < 0 or score < _threshold():
            return None
        cached_question = _items[position][0]
        requested_tokens = _salient_tokens(question)
        cached_tokens = _salient_tokens(cached_question)
        if requested_tokens and not requested_tokens.issubset(cached_tokens):
            return None
        try:
            cached = json.loads(_items[position][1])
        except json.JSONDecodeError:
            return None
        if not isinstance(cached, dict):
            return None
        cached["cache_hit"] = True
        cached["cache_similarity"] = round(score, 4)
        return cached


def clear() -> None:
    global _index, _vectors_ready, _unavailable
    with _lock:
        _items.clear()
        _index = None
        _vectors_ready = False
        _unavailable = False
