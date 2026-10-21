from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import sqlglot
from validation_helpers import check

EXAMPLES_PATH = ROOT / "examples" / "question_sql_examples.json"
DB_PATH = ROOT / "data" / "querymind.sqlite3"
MINIMUM_EXAMPLES = 30
QUERY_TYPES = {"SELECT_SIMPLE", "SELECT_AGGREGATE", "SELECT_JOIN", "SELECT_TEMPORAL"}



def main() -> int:
    examples = json.loads(EXAMPLES_PATH.read_text(encoding="utf-8"))
    check(len(examples) >= MINIMUM_EXAMPLES, f"library has at least {MINIMUM_EXAMPLES} examples ({len(examples)})")
    ids = [example["id"] for example in examples]
    check(len(ids) == len(set(ids)), "example ids are unique")
    questions = [example["question"].strip().lower() for example in examples]
    check(len(questions) == len(set(questions)), "example questions are unique")
    counts = Counter(example["query_type"] for example in examples)
    check(set(counts) == QUERY_TYPES, f"all four query types are covered: {dict(counts)}")

    with sqlite3.connect(DB_PATH) as connection:
        for example in examples:
            statements = sqlglot.parse(example["sql"], read="sqlite")
            check(len(statements) == 1, f"{example['id']}: parses as a single statement")
            try:
                connection.execute(example["sql"]).fetchall()
            except sqlite3.Error as error:
                raise AssertionError(f"{example['id']}: SQL failed to execute: {error}") from None
    print("[PASS] every example executes against the demo database")
    print("Examples library validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
