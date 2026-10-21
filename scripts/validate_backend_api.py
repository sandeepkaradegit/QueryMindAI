from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx
from sqlalchemy import text


ROOT = Path(__file__).resolve().parents[1]
BACKEND_PATH = ROOT / "backend"
if str(BACKEND_PATH) not in sys.path:
    sys.path.insert(0, str(BACKEND_PATH))

from app.database import create_database_engine
from app.main import app
from app.auth import get_current_user
from app import admin_store

import os, tempfile
from validation_helpers import check
os.environ["QUERYMIND_ADMIN_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "admin.sqlite3")
app.dependency_overrides[get_current_user] = lambda: admin_store.AdminUserRecord(
    id=1, username="test-admin", password_hash="", password_salt="", role="admin", created_at=""
)



async def validate_api() -> None:
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            health = await client.get("/health")
            check(health.status_code == 200, "health endpoint responds successfully")
            check(health.json() == {"status": "ok"}, "health response matches its typed contract")

            invalid_query = await client.post("/query", json={"question": "   "})
            check(invalid_query.status_code == 422, "invalid query input is rejected by request validation")

            oversized_query = await client.post("/query", json={"question": "q" * 2001})
            check(oversized_query.status_code == 422, "questions longer than 2,000 characters are rejected")
    finally:
        pass


def validate_database_connection() -> None:
    database_path = ROOT / "data" / "querymind.sqlite3"
    check(database_path.is_file(), "SQLite demo fixture exists")
    engine = create_database_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        with engine.connect() as connection:
            customer_count = connection.execute(text("SELECT COUNT(*) FROM customers")).scalar_one()
        check(customer_count == 1200, "SQLAlchemy connects and reads the seeded SQLite fixture")
    finally:
        engine.dispose()


def main() -> None:
    asyncio.run(validate_api())
    validate_database_connection()
    print("Backend API validation passed.")


if __name__ == "__main__":
    main()