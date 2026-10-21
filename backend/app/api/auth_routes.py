from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app import admin_store
from app.auth import (
    SESSION_COOKIE_NAME,
    create_session,
    get_current_user,
    hash_password,
    revoke_session,
    verify_password,
)
from app.config import get_session_ttl_seconds
from app.schemas.auth import LoginRequest, UserResponse

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=get_session_ttl_seconds(),
        path="/",
    )


def _validated_username(raw_username: str) -> str:
    username = raw_username.strip()
    if not username:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Username must not be blank.",
        )
    return username


@router.post("/bootstrap", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def bootstrap_admin_user(request: LoginRequest, response: Response) -> UserResponse:
    username = _validated_username(request.username)
    if admin_store.count_admin_users() > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An admin user already exists; bootstrap can only run once.",
        )
    if len(request.password) < 8:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Password must be at least 8 characters long.",
        )
    password_hash, password_salt = hash_password(request.password)
    user = admin_store.create_admin_user(
        username=username,
        password_hash=password_hash,
        password_salt=password_salt,
        role="admin",
    )
    token = create_session(user.id, ttl_seconds=get_session_ttl_seconds())
    _set_session_cookie(response, token)
    return UserResponse(username=user.username, role=user.role)


@router.post("/login", response_model=UserResponse)
def login(request: LoginRequest, response: Response) -> UserResponse:
    username = _validated_username(request.username)
    user = admin_store.get_admin_user_by_username(username)
    if user is None or not verify_password(request.password, user.password_hash, user.password_salt):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password.",
        )
    token = create_session(user.id, ttl_seconds=get_session_ttl_seconds())
    _set_session_cookie(response, token)
    return UserResponse(username=user.username, role=user.role)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response) -> None:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        revoke_session(token)
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")


@router.get("/me", response_model=UserResponse)
def get_me(user: admin_store.AdminUserRecord = Depends(get_current_user)) -> UserResponse:
    return UserResponse(username=user.username, role=user.role)
