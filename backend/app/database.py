from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, URL, make_url

from app import admin_store


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "querymind.sqlite3"
DEFAULT_DATABASE_URL = URL.create("sqlite", database=str(DEFAULT_DB_PATH))


def create_database_engine(database_url: str | None = None) -> Engine:
    configured_url = database_url or os.environ.get("QUERYMIND_DATABASE_URL")
    url = make_url(configured_url) if configured_url else DEFAULT_DATABASE_URL
    connect_args = {"check_same_thread": False} if url.get_backend_name() == "sqlite" else {}
    return create_engine(url, connect_args=connect_args, pool_pre_ping=True)


def get_database_engine() -> Iterator[Engine]:
    active_connection = admin_store.get_active_database_connection()
    database_url = active_connection.connection_url if active_connection else None
    engine = create_database_engine(database_url)
    try:
        yield engine
    finally:
        engine.dispose()