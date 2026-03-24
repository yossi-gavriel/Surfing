import hashlib
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Annotated, Any

import jwt
from fastapi import Header, HTTPException, Request, status


JWT_ALGORITHM = "HS256"


@dataclass(frozen=True)
class JWTConfig:
    secret: str
    expire_hours: int
    issuer: str
    audience: str
    leeway_seconds: int


@lru_cache
def get_jwt_config() -> JWTConfig:
    secret = os.environ.get("JWT_SECRET", "").strip()
    lowered_secret = secret.lower()
    if (
        not secret
        or len(secret) < 32
        or "change-me" in lowered_secret
        or lowered_secret.startswith("replace-with")
        or lowered_secret == "surf-ai-dev-secret"
    ):
        raise RuntimeError("JWT_SECRET must be set to a strong value with at least 32 characters")

    return JWTConfig(
        secret=secret,
        expire_hours=int(os.environ.get("JWT_EXPIRE_HOURS", "168")),
        issuer=os.environ.get("JWT_ISSUER", "surf-ai-api"),
        audience=os.environ.get("JWT_AUDIENCE", "surf-ai-users"),
        leeway_seconds=int(os.environ.get("JWT_LEEWAY_SECONDS", "30")),
    )


def normalize_email(email: str) -> str:
    return email.strip().lower()


def is_admin_email(email: str) -> bool:
    configured = os.environ.get("ADMIN_EMAILS", "").strip()
    if not configured:
        return False

    normalized_email = normalize_email(email)
    allowed = {
        normalize_email(item)
        for item in configured.split(",")
        if item.strip()
    }
    return normalized_email in allowed


def hash_password(password: str, salt_hex: str | None = None) -> tuple[str, str]:
    salt = bytes.fromhex(salt_hex) if salt_hex else os.urandom(16)
    password_hash = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        100_000,
    ).hex()
    return salt.hex(), password_hash


def verify_password(password: str, password_salt: str, password_hash: str) -> bool:
    _, candidate_hash = hash_password(password, salt_hex=password_salt)
    return candidate_hash == password_hash


def create_access_token(user: dict[str, Any]) -> str:
    try:
        config = get_jwt_config()
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is temporarily unavailable",
        ) from exc
    issued_at = datetime.now(timezone.utc)
    expires_at = issued_at + timedelta(hours=config.expire_hours)
    payload = {
        "sub": user["user_id"],
        "email": user["email"],
        "role": user.get("role", "user"),
        "pool_id": user.get("pool_id"),
        "iss": config.issuer,
        "aud": config.audience,
        "iat": issued_at,
        "nbf": issued_at,
        "exp": expires_at,
    }
    return jwt.encode(payload, config.secret, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    try:
        config = get_jwt_config()
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is temporarily unavailable",
        ) from exc
    try:
        payload = jwt.decode(
            token,
            config.secret,
            algorithms=[JWT_ALGORITHM],
            audience=config.audience,
            issuer=config.issuer,
            leeway=config.leeway_seconds,
            options={
                "require": ["sub", "email", "iss", "aud", "iat", "nbf", "exp"],
            },
        )
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expired",
        ) from exc
    except jwt.PyJWTError as exc:  # pragma: no cover - library errors vary
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        ) from exc

    if not isinstance(payload.get("sub"), str) or not payload["sub"]:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token subject",
        )

    return payload


def is_admin_user(user: dict[str, Any]) -> bool:
    return user.get("role") == "admin" or is_admin_email(user.get("email", ""))


def _extract_bearer_token(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authorization header",
        )

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization header",
        )
    return token


def get_current_user(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    token = _extract_bearer_token(authorization)
    payload = decode_access_token(token)
    db = getattr(request.app.state, "db", None)
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API database is unavailable",
        )
    user = db.get_user_by_id(payload["sub"])
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )
    return user
