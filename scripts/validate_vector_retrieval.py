from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ["QUERYMIND_ADMIN_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "admin.sqlite3")
os.environ["QUERYMIND_EXAMPLE_RETRIEVAL"] = "vector"

from app.services import vector_index
from app.services.example_retriever import load_example_library, retrieve_examples
from validation_helpers import check



def main() -> int:
    examples = load_example_library()
    documents = [(example.id, example.question) for example in examples]
    check(vector_index.search("total sales", documents, 3) is not None, "FAISS/fastembed vector search is available")

    ranked = retrieve_examples("What is the revenue per product category?", examples, top_k=3)
    check(bool(ranked) and ranked[0].id == "revenue-by-category", "paraphrase retrieves the semantically matching example")
    check(all(0.0 < example.similarity <= 1.0 for example in ranked), "similarities are cosine scores")

    check(not retrieve_examples("Explain volcanic eruptions", examples, top_k=3), "unrelated questions retrieve nothing")

    hits = vector_index.search("Explain volcanic eruptions", documents, 3, threshold=0.0)
    check(len(hits or []) == 3, "a zero threshold ranks without filtering (used by admin search)")

    os.environ["QUERYMIND_EXAMPLE_RETRIEVAL"] = "lexical"
    check(vector_index.search("total sales", documents, 3) is None, "lexical mode disables vector search")
    check(bool(retrieve_examples("Show monthly sales by product", examples, top_k=3)), "lexical fallback still retrieves")
    print("Vector retrieval validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
