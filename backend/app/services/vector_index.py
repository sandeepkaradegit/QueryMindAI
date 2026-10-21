"""Embedding-based (FAISS) similarity search over question/SQL examples.

Embeddings come from a local ONNX model through fastembed, so no external service is needed after the
first model download. Any failure (missing packages, no network for the first download) makes
``search`` return ``None`` so callers can fall back to lexical retrieval.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Sequence

logger = logging.getLogger(__name__)

DEFAULT_EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
DEFAULT_MIN_SIMILARITY = 0.6

_lock = threading.Lock()
_embedder = None
_unavailable = False
_index_cache: tuple[tuple[tuple[str, str], ...], object] | None = None


def retrieval_mode() -> str:
    return os.getenv("QUERYMIND_EXAMPLE_RETRIEVAL", "vector").strip().lower()


def min_similarity() -> float:
    try:
        return float(os.getenv("QUERYMIND_VECTOR_MIN_SIMILARITY", DEFAULT_MIN_SIMILARITY))
    except ValueError:
        return DEFAULT_MIN_SIMILARITY


def _get_embedder():
    global _embedder
    if _embedder is None:
        from fastembed import TextEmbedding

        _embedder = TextEmbedding(os.getenv("QUERYMIND_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL))
    return _embedder


def _embed(texts: Sequence[str]):
    import faiss
    import numpy as np

    vectors = np.array(list(_get_embedder().embed(list(texts))), dtype="float32")
    faiss.normalize_L2(vectors)
    return vectors


def _get_index(documents: Sequence[tuple[str, str]]):
    global _index_cache
    signature = tuple(documents)
    if _index_cache is not None and _index_cache[0] == signature:
        return _index_cache[1]
    import faiss

    vectors = _embed([question for _, question in documents])
    index = faiss.IndexFlatIP(vectors.shape[1])
    index.add(vectors)
    _index_cache = (signature, index)
    return index


def search(
    query: str,
    documents: Sequence[tuple[str, str]],
    top_k: int,
    threshold: float | None = None,
) -> list[tuple[str, float]] | None:
    """Return ``(document_id, cosine_similarity)`` pairs, best first, or ``None`` if vectors are unavailable."""
    global _unavailable
    if _unavailable or retrieval_mode() != "vector":
        return None
    if not documents or top_k <= 0 or not query.strip():
        return []
    cutoff = min_similarity() if threshold is None else threshold
    try:
        with _lock:
            index = _get_index(documents)
            scores, positions = index.search(_embed([query]), min(top_k, len(documents)))
    except Exception:
        logger.warning("Vector retrieval unavailable; falling back to lexical retrieval.", exc_info=True)
        _unavailable = True
        return None
    return [
        (documents[int(position)][0], float(score))
        for score, position in zip(scores[0], positions[0])
        if position >= 0 and score >= cutoff
    ]
