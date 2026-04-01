from collections.abc import AsyncGenerator

from fastapi import Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_db_session

settings = get_settings()


async def db_session_dep() -> AsyncGenerator[AsyncSession, None]:
    async for session in get_db_session():
        yield session


async def trusted_student_email_dep(
    x_edusense_internal_key: str | None = Header(default=None),
    x_edusense_student_email: str | None = Header(default=None),
) -> str:
    expected_key = settings.internal_api_key.strip()
    if not expected_key:
        raise HTTPException(status_code=500, detail="INTERNAL_API_KEY is not configured on the backend.")
    if x_edusense_internal_key != expected_key:
        raise HTTPException(status_code=401, detail="Unauthorized student access.")
    student_email = (x_edusense_student_email or "").strip().lower()
    if not student_email:
        raise HTTPException(status_code=400, detail="Student identity header is missing.")
    return student_email
