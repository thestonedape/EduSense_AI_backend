from collections.abc import AsyncGenerator
import base64
import hashlib
import hmac
import json
import time

from fastapi import Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_db_session

settings = get_settings()

def verify_token(authorization: str | None) -> dict:
    if not settings.internal_api_key.strip():
        raise HTTPException(503, 'Authentication is not configured')
    try:
        scheme, raw = (authorization or '').split(' ', 1)
        if scheme.lower() != 'bearer': raise ValueError()
        payload, signature = raw.split('.')
        expected = base64.urlsafe_b64encode(hmac.digest(settings.internal_api_key.encode(), payload.encode(), 'sha256')).rstrip(b'=').decode()
        if not hmac.compare_digest(signature, expected): raise ValueError()
        claims = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
        if claims['exp'] <= time.time() or claims['exp'] > time.time() + 360 or claims['role'] not in {'admin', 'student'} or not isinstance(claims['email'], str) or not claims['email'].strip(): raise ValueError()
        if not isinstance(claims.get('demo', False), bool): raise ValueError()
        return claims
    except (ValueError, KeyError, TypeError):
        raise HTTPException(401, 'Unauthorized') from None

SAFE_METHODS = {'GET', 'HEAD', 'OPTIONS'}


def demo_write_blocked(method: str, authorization: str | None) -> bool:
    """True when a validly signed demo token attempts a write; demo identities are read-only."""
    if method.upper() in SAFE_METHODS or not authorization:
        return False
    try:
        return verify_token(authorization).get('demo') is True
    except HTTPException:
        return False  # invalid tokens are rejected by the route's own auth dependency


async def authenticated_dep(authorization: str | None = Header(default=None)):
    return verify_token(authorization)

async def admin_dep(authorization: str | None = Header(default=None)):
    claims = verify_token(authorization)
    if claims['role'] != 'admin': raise HTTPException(403, 'Admin access required')
    return claims


async def db_session_dep() -> AsyncGenerator[AsyncSession, None]:
    async for session in get_db_session():
        yield session


async def trusted_student_email_dep(
    x_edusense_internal_key: str | None = Header(default=None),
    x_edusense_student_email: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> str:
    if authorization:
        return verify_token(authorization)['email'].strip().lower()
    expected_key = settings.internal_api_key.strip()
    if not expected_key:
        raise HTTPException(status_code=500, detail="INTERNAL_API_KEY is not configured on the backend.")
    if not hmac.compare_digest(x_edusense_internal_key or '', expected_key):
        raise HTTPException(status_code=401, detail="Unauthorized student access.")
    student_email = (x_edusense_student_email or "").strip().lower()
    if not student_email:
        raise HTTPException(status_code=400, detail="Student identity header is missing.")
    return student_email
