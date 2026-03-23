from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, Request, status

from shared.utils.logger import get_logger
from src.security import (
    create_access_token,
    hash_password,
    normalize_email,
    verify_password,
)

logger = get_logger("api-auth")

router = APIRouter()


class AuthRequest(BaseModel):
    email: str
    password: str


@router.post("/signup")
def signup(payload: AuthRequest, request: Request) -> dict[str, str | None]:
    request.app.state.metrics.increment("auth.signup.attempt")
    email = normalize_email(payload.email)
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Valid email is required")
    if len(payload.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")

    existing_user = request.app.state.db.get_user_by_email(email)
    if existing_user:
        request.app.state.metrics.increment("auth.signup.conflict")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    password_salt, password_hash = hash_password(payload.password)
    user = request.app.state.db.create_user(
        email=email,
        password_hash=password_hash,
        password_salt=password_salt,
    )
    if not user:
        request.app.state.metrics.increment("auth.signup.conflict")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )
    token = create_access_token(user)
    request.app.state.metrics.increment("auth.signup.success")
    logger.info("User signed up: user_id=%s", user["user_id"])
    return {
        "user_id": user["user_id"],
        "token": token,
        "email": user["email"],
        "role": user.get("role", "user"),
        "pool_id": user.get("pool_id"),
    }


@router.post("/login")
def login(payload: AuthRequest, request: Request) -> dict[str, str | None]:
    request.app.state.metrics.increment("auth.login.attempt")
    email = normalize_email(payload.email)
    user = request.app.state.db.get_user_by_email(email)
    if not user or not verify_password(
        payload.password,
        user.get("password_salt", ""),
        user.get("password_hash", ""),
    ):
        request.app.state.metrics.increment("auth.login.failure")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    token = create_access_token(user)
    request.app.state.metrics.increment("auth.login.success")
    logger.info("User logged in: user_id=%s", user["user_id"])
    return {
        "user_id": user["user_id"],
        "token": token,
        "email": user["email"],
        "role": user.get("role", "user"),
        "pool_id": user.get("pool_id"),
    }
