from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ADMIN_DB_PATH = PROJECT_ROOT / "data" / "querymind_admin.sqlite3"

# column_name uses '' (not NULL) for table-level annotations so the UNIQUE
# constraint can detect duplicates; SQLite treats every NULL as distinct.
_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS database_connections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        dialect TEXT NOT NULL,
        connection_url TEXT NOT NULL,
        is_active INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS schema_annotations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        table_name TEXT NOT NULL,
        column_name TEXT NOT NULL DEFAULT '',
        description TEXT,
        synonyms TEXT NOT NULL DEFAULT '[]',
        is_sensitive INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(table_name, column_name)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS model_config (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        provider TEXT NOT NULL DEFAULT 'groq',
        model TEXT,
        guardrail_model TEXT,
        temperature REAL NOT NULL DEFAULT 0.0,
        max_tokens INTEGER,
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS admin_users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        password_salt TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'admin',
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sessions (
        token_hash TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES admin_users(id) ON DELETE CASCADE,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        expires_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS examples (
        id TEXT PRIMARY KEY,
        question TEXT NOT NULL,
        sql TEXT NOT NULL,
        query_type TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS query_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trace_id TEXT,
        question TEXT NOT NULL,
        status TEXT NOT NULL,
        sql TEXT,
        error_reason TEXT,
        row_count INTEGER,
        latency_ms REAL,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS guardrails_config (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        max_rows INTEGER NOT NULL DEFAULT 100,
        allow_write_operations INTEGER NOT NULL DEFAULT 0,
        allowed_table_prefixes TEXT NOT NULL DEFAULT '[]',
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS value_normalizations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        table_name TEXT NOT NULL,
        column_name TEXT NOT NULL,
        alias_value TEXT NOT NULL,
        canonical_value TEXT NOT NULL,
        updated_at TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(table_name, column_name, alias_value)
    )
    """,
)


@dataclass(frozen=True)
class DatabaseConnectionRecord:
    id: int
    name: str
    dialect: str
    connection_url: str
    is_active: bool
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class SchemaAnnotationRecord:
    id: int
    table_name: str
    column_name: str | None
    description: str | None
    synonyms: list[str]
    is_sensitive: bool
    updated_at: str


@dataclass(frozen=True)
class ModelConfigRecord:
    provider: str
    model: str | None
    guardrail_model: str | None
    temperature: float
    max_tokens: int | None
    updated_at: str
    transpile_dialect: str | None = None


@dataclass(frozen=True)
class AdminUserRecord:
    id: int
    username: str
    password_hash: str
    password_salt: str
    role: str
    created_at: str


@dataclass(frozen=True)
class ExampleRecord:
    id: str
    question: str
    sql: str
    query_type: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class QueryLogRecord:
    id: int
    trace_id: str | None
    question: str
    status: str
    sql: str | None
    error_reason: str | None
    row_count: int | None
    latency_ms: float | None
    created_at: str


@dataclass(frozen=True)
class GuardrailsConfigRecord:
    max_rows: int
    allow_write_operations: bool
    allowed_table_prefixes: list[str]
    updated_at: str


@dataclass(frozen=True)
class ValueNormalizationRecord:
    id: int
    table_name: str
    column_name: str
    alias_value: str
    canonical_value: str
    updated_at: str


def _admin_db_path() -> Path:
    configured = os.environ.get("QUERYMIND_ADMIN_DB_PATH", "").strip()
    if not configured:
        return DEFAULT_ADMIN_DB_PATH
    # Accept a SQLAlchemy-style "sqlite:///path" URL as well as a plain file path.
    if configured.lower().startswith("sqlite:///"):
        configured = configured[len("sqlite:///"):]
    path = Path(configured)
    return path if path.is_absolute() else DEFAULT_ADMIN_DB_PATH.parents[1] / path


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    db_path = _admin_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        for statement in _SCHEMA_STATEMENTS:
            connection.execute(statement)
        model_columns = {row["name"] for row in connection.execute("PRAGMA table_info(model_config)")}
        if "transpile_dialect" not in model_columns:
            connection.execute("ALTER TABLE model_config ADD COLUMN transpile_dialect TEXT")
        connection.execute(
            "INSERT OR IGNORE INTO model_config (id, provider, temperature) VALUES (1, 'groq', 0.0)"
        )
        connection.execute(
            "INSERT OR IGNORE INTO guardrails_config (id, max_rows, allow_write_operations, allowed_table_prefixes)"
            " VALUES (1, 100, 0, '[]')"
        )
        connection.commit()
        yield connection
    finally:
        connection.close()


def _row_to_connection(row: sqlite3.Row) -> DatabaseConnectionRecord:
    return DatabaseConnectionRecord(
        id=row["id"],
        name=row["name"],
        dialect=row["dialect"],
        connection_url=row["connection_url"],
        is_active=bool(row["is_active"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def list_database_connections() -> list[DatabaseConnectionRecord]:
    with _connect() as connection:
        rows = connection.execute("SELECT * FROM database_connections ORDER BY id").fetchall()
        return [_row_to_connection(row) for row in rows]


def create_database_connection(*, name: str, dialect: str, connection_url: str) -> DatabaseConnectionRecord:
    with _connect() as connection:
        cursor = connection.execute(
            "INSERT INTO database_connections (name, dialect, connection_url) VALUES (?, ?, ?)",
            (name, dialect, connection_url),
        )
        connection.commit()
        row = connection.execute(
            "SELECT * FROM database_connections WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
        return _row_to_connection(row)


def activate_database_connection(connection_id: int) -> DatabaseConnectionRecord | None:
    with _connect() as connection:
        existing = connection.execute(
            "SELECT id FROM database_connections WHERE id = ?", (connection_id,)
        ).fetchone()
        if existing is None:
            return None
        connection.execute("UPDATE database_connections SET is_active = 0")
        connection.execute(
            "UPDATE database_connections SET is_active = 1, updated_at = datetime('now') WHERE id = ?",
            (connection_id,),
        )
        connection.commit()
        row = connection.execute(
            "SELECT * FROM database_connections WHERE id = ?", (connection_id,)
        ).fetchone()
        return _row_to_connection(row)


def delete_database_connection(connection_id: int) -> bool:
    with _connect() as connection:
        cursor = connection.execute("DELETE FROM database_connections WHERE id = ?", (connection_id,))
        connection.commit()
        return cursor.rowcount > 0


def get_active_database_connection() -> DatabaseConnectionRecord | None:
    with _connect() as connection:
        row = connection.execute(
            "SELECT * FROM database_connections WHERE is_active = 1 LIMIT 1"
        ).fetchone()
        return _row_to_connection(row) if row else None


def _row_to_annotation(row: sqlite3.Row) -> SchemaAnnotationRecord:
    return SchemaAnnotationRecord(
        id=row["id"],
        table_name=row["table_name"],
        column_name=row["column_name"] or None,
        description=row["description"],
        synonyms=json.loads(row["synonyms"]),
        is_sensitive=bool(row["is_sensitive"]),
        updated_at=row["updated_at"],
    )


def list_schema_annotations() -> list[SchemaAnnotationRecord]:
    with _connect() as connection:
        rows = connection.execute(
            "SELECT * FROM schema_annotations ORDER BY table_name, column_name"
        ).fetchall()
        return [_row_to_annotation(row) for row in rows]


def upsert_schema_annotation(
    *,
    table_name: str,
    column_name: str | None,
    description: str | None,
    synonyms: list[str],
    is_sensitive: bool,
) -> SchemaAnnotationRecord:
    normalized_column = column_name or ""
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO schema_annotations (table_name, column_name, description, synonyms, is_sensitive, updated_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(table_name, column_name) DO UPDATE SET
                description = excluded.description,
                synonyms = excluded.synonyms,
                is_sensitive = excluded.is_sensitive,
                updated_at = datetime('now')
            """,
            (table_name, normalized_column, description, json.dumps(synonyms), int(is_sensitive)),
        )
        connection.commit()
        row = connection.execute(
            "SELECT * FROM schema_annotations WHERE table_name = ? AND column_name = ?",
            (table_name, normalized_column),
        ).fetchone()
        return _row_to_annotation(row)


def delete_schema_annotation(annotation_id: int) -> bool:
    with _connect() as connection:
        cursor = connection.execute("DELETE FROM schema_annotations WHERE id = ?", (annotation_id,))
        connection.commit()
        return cursor.rowcount > 0


def _row_to_value_normalization(row: sqlite3.Row) -> ValueNormalizationRecord:
    return ValueNormalizationRecord(
        id=row["id"],
        table_name=row["table_name"],
        column_name=row["column_name"],
        alias_value=row["alias_value"],
        canonical_value=row["canonical_value"],
        updated_at=row["updated_at"],
    )


def list_value_normalizations(*, table_name: str | None = None) -> list[ValueNormalizationRecord]:
    with _connect() as connection:
        if table_name:
            rows = connection.execute(
                """
                SELECT * FROM value_normalizations
                WHERE table_name = ?
                ORDER BY table_name, column_name, alias_value
                """,
                (table_name,),
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT * FROM value_normalizations ORDER BY table_name, column_name, alias_value"
            ).fetchall()
        return [_row_to_value_normalization(row) for row in rows]


def upsert_value_normalization(
    *,
    table_name: str,
    column_name: str,
    alias_value: str,
    canonical_value: str,
) -> ValueNormalizationRecord:
    normalized_alias = alias_value.strip().lower()
    normalized_canonical = canonical_value.strip()
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO value_normalizations (table_name, column_name, alias_value, canonical_value, updated_at)
            VALUES (?, ?, ?, ?, datetime('now'))
            ON CONFLICT(table_name, column_name, alias_value) DO UPDATE SET
                canonical_value = excluded.canonical_value,
                updated_at = datetime('now')
            """,
            (table_name, column_name, normalized_alias, normalized_canonical),
        )
        connection.commit()
        row = connection.execute(
            """
            SELECT * FROM value_normalizations
            WHERE table_name = ? AND column_name = ? AND alias_value = ?
            """,
            (table_name, column_name, normalized_alias),
        ).fetchone()
        return _row_to_value_normalization(row)


def delete_value_normalization(normalization_id: int) -> bool:
    with _connect() as connection:
        cursor = connection.execute("DELETE FROM value_normalizations WHERE id = ?", (normalization_id,))
        connection.commit()
        return cursor.rowcount > 0


def _row_to_model_config(row: sqlite3.Row) -> ModelConfigRecord:
    return ModelConfigRecord(
        provider=row["provider"],
        model=row["model"],
        guardrail_model=row["guardrail_model"],
        temperature=row["temperature"],
        max_tokens=row["max_tokens"],
        updated_at=row["updated_at"],
        transpile_dialect=row["transpile_dialect"],
    )


def get_model_config() -> ModelConfigRecord:
    with _connect() as connection:
        row = connection.execute("SELECT * FROM model_config WHERE id = 1").fetchone()
        return _row_to_model_config(row)


def update_model_config(
    *,
    provider: str,
    model: str | None,
    guardrail_model: str | None,
    temperature: float,
    max_tokens: int | None,
    transpile_dialect: str | None = None,
) -> ModelConfigRecord:
    with _connect() as connection:
        connection.execute(
            """
            UPDATE model_config
            SET provider = ?, model = ?, guardrail_model = ?, temperature = ?, max_tokens = ?,
                transpile_dialect = ?, updated_at = datetime('now')
            WHERE id = 1
            """,
            (provider, model, guardrail_model, temperature, max_tokens, transpile_dialect),
        )
        connection.commit()
        row = connection.execute("SELECT * FROM model_config WHERE id = 1").fetchone()
        return _row_to_model_config(row)


def _row_to_user(row: sqlite3.Row) -> AdminUserRecord:
    return AdminUserRecord(
        id=row["id"],
        username=row["username"],
        password_hash=row["password_hash"],
        password_salt=row["password_salt"],
        role=row["role"],
        created_at=row["created_at"],
    )


def count_admin_users() -> int:
    with _connect() as connection:
        row = connection.execute("SELECT COUNT(*) AS total FROM admin_users").fetchone()
        return int(row["total"])


def _normalize_username(username: str) -> str:
    return username.strip()


def create_admin_user(*, username: str, password_hash: str, password_salt: str, role: str = "admin") -> AdminUserRecord:
    normalized_username = _normalize_username(username)
    with _connect() as connection:
        cursor = connection.execute(
            "INSERT INTO admin_users (username, password_hash, password_salt, role) VALUES (?, ?, ?, ?)",
            (normalized_username, password_hash, password_salt, role),
        )
        connection.commit()
        row = connection.execute("SELECT * FROM admin_users WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return _row_to_user(row)


def get_admin_user_by_username(username: str) -> AdminUserRecord | None:
    normalized_username = _normalize_username(username)
    with _connect() as connection:
        row = connection.execute(
            "SELECT * FROM admin_users WHERE TRIM(username) = ? COLLATE NOCASE",
            (normalized_username,),
        ).fetchone()
        return _row_to_user(row) if row else None


def get_admin_user_by_id(user_id: int) -> AdminUserRecord | None:
    with _connect() as connection:
        row = connection.execute("SELECT * FROM admin_users WHERE id = ?", (user_id,)).fetchone()
        return _row_to_user(row) if row else None


def create_session(*, token_hash: str, user_id: int, expires_at: str) -> None:
    with _connect() as connection:
        connection.execute(
            "INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
            (token_hash, user_id, expires_at),
        )
        connection.commit()


def get_session_user(token_hash: str) -> AdminUserRecord | None:
    with _connect() as connection:
        row = connection.execute(
            """
            SELECT admin_users.* FROM sessions
            JOIN admin_users ON admin_users.id = sessions.user_id
            WHERE sessions.token_hash = ? AND sessions.expires_at > datetime('now')
            """,
            (token_hash,),
        ).fetchone()
        return _row_to_user(row) if row else None


def delete_session(token_hash: str) -> None:
    with _connect() as connection:
        connection.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
        connection.commit()


def _row_to_example(row: sqlite3.Row) -> ExampleRecord:
    return ExampleRecord(
        id=row["id"],
        question=row["question"],
        sql=row["sql"],
        query_type=row["query_type"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def count_examples() -> int:
    with _connect() as connection:
        row = connection.execute("SELECT COUNT(*) AS total FROM examples").fetchone()
        return int(row["total"])


def seed_examples_if_empty(examples: list[dict[str, str]]) -> None:
    """Import JSON-library examples the store has never seen; admin deletions and edits stick."""
    with _connect() as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS example_seed_ids (id TEXT PRIMARY KEY)")
        if not connection.execute("SELECT 1 FROM example_seed_ids LIMIT 1").fetchone():
            # Legacy stores seeded before tracking existed: treat current rows as already imported.
            connection.execute("INSERT OR IGNORE INTO example_seed_ids (id) SELECT id FROM examples")
        for example in examples:
            seen = connection.execute(
                "SELECT 1 FROM example_seed_ids WHERE id = ?", (example["id"],)
            ).fetchone()
            if seen:
                continue
            connection.execute(
                "INSERT OR IGNORE INTO examples (id, question, sql, query_type) VALUES (?, ?, ?, ?)",
                (example["id"], example["question"], example["sql"], example["query_type"]),
            )
            connection.execute("INSERT OR IGNORE INTO example_seed_ids (id) VALUES (?)", (example["id"],))
        connection.commit()


def list_examples() -> list[ExampleRecord]:
    with _connect() as connection:
        rows = connection.execute("SELECT * FROM examples ORDER BY id").fetchall()
        return [_row_to_example(row) for row in rows]


def create_example(*, id: str, question: str, sql: str, query_type: str) -> ExampleRecord:
    with _connect() as connection:
        connection.execute(
            "INSERT INTO examples (id, question, sql, query_type) VALUES (?, ?, ?, ?)",
            (id, question, sql, query_type),
        )
        connection.commit()
        row = connection.execute("SELECT * FROM examples WHERE id = ?", (id,)).fetchone()
        return _row_to_example(row)


def update_example(
    example_id: str,
    *,
    question: str,
    sql: str,
    query_type: str,
) -> ExampleRecord | None:
    with _connect() as connection:
        connection.execute(
            """
            UPDATE examples SET question = ?, sql = ?, query_type = ?, updated_at = datetime('now')
            WHERE id = ?
            """,
            (question, sql, query_type, example_id),
        )
        connection.commit()
        row = connection.execute("SELECT * FROM examples WHERE id = ?", (example_id,)).fetchone()
        return _row_to_example(row) if row else None


def delete_example(example_id: str) -> bool:
    with _connect() as connection:
        cursor = connection.execute("DELETE FROM examples WHERE id = ?", (example_id,))
        connection.commit()
        return cursor.rowcount > 0


def _row_to_query_log(row: sqlite3.Row) -> QueryLogRecord:
    return QueryLogRecord(
        id=row["id"],
        trace_id=row["trace_id"],
        question=row["question"],
        status=row["status"],
        sql=row["sql"],
        error_reason=row["error_reason"],
        row_count=row["row_count"],
        latency_ms=row["latency_ms"],
        created_at=row["created_at"],
    )


def record_query_log(
    *,
    trace_id: str | None,
    question: str,
    status: str,
    sql: str | None = None,
    error_reason: str | None = None,
    row_count: int | None = None,
    latency_ms: float | None = None,
) -> None:
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO query_logs (trace_id, question, status, sql, error_reason, row_count, latency_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (trace_id, question, status, sql, error_reason, row_count, latency_ms),
        )
        connection.commit()


def list_query_logs(
    *,
    status_filter: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[QueryLogRecord]:
    query = "SELECT * FROM query_logs"
    parameters: list[object] = []
    if status_filter:
        query += " WHERE status = ?"
        parameters.append(status_filter)
    query += " ORDER BY id DESC LIMIT ? OFFSET ?"
    parameters.extend([limit, offset])
    with _connect() as connection:
        rows = connection.execute(query, parameters).fetchall()
        return [_row_to_query_log(row) for row in rows]


def _row_to_guardrails_config(row: sqlite3.Row) -> GuardrailsConfigRecord:
    return GuardrailsConfigRecord(
        max_rows=row["max_rows"],
        allow_write_operations=bool(row["allow_write_operations"]),
        allowed_table_prefixes=json.loads(row["allowed_table_prefixes"]),
        updated_at=row["updated_at"],
    )


def get_guardrails_config() -> GuardrailsConfigRecord:
    with _connect() as connection:
        row = connection.execute("SELECT * FROM guardrails_config WHERE id = 1").fetchone()
        return _row_to_guardrails_config(row)


def update_guardrails_config(
    *,
    max_rows: int,
    allowed_table_prefixes: list[str],
) -> GuardrailsConfigRecord:
    with _connect() as connection:
        connection.execute(
            """
            UPDATE guardrails_config
            SET max_rows = ?, allowed_table_prefixes = ?, updated_at = datetime('now')
            WHERE id = 1
            """,
            (max_rows, json.dumps(allowed_table_prefixes)),
        )
        connection.commit()
        row = connection.execute("SELECT * FROM guardrails_config WHERE id = 1").fetchone()
        return _row_to_guardrails_config(row)
