from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request, status

from app import admin_store


SESSION_COOKIE_NAME = "qm_session"
_PBKDF2_ITERATIONS = 200_000


def hash_password(password: str, *, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), _PBKDF2_ITERATIONS)
    return derived.hex(), salt


def verify_password(password: str, password_hash: str, password_salt: str) -> bool:
    candidate_hash, _ = hash_password(password, salt=password_salt)
    return secrets.compare_digest(candidate_hash, password_hash)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(user_id: int, *, ttl_seconds: int) -> str:
    token = secrets.token_urlsafe(32)
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).strftime("%Y-%m-%d %H:%M:%S")
    admin_store.create_session(token_hash=_hash_token(token), user_id=user_id, expires_at=expires_at)
    return token


def revoke_session(token: str) -> None:
    admin_store.delete_session(_hash_token(token))


def get_current_user(request: Request) -> admin_store.AdminUserRecord:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication is required.")
    user = admin_store.get_session_user(_hash_token(token))
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="The session is invalid or has expired.")
    return user
