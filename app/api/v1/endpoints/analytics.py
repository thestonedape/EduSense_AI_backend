from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import db_session_dep
from app.schemas.analytics import AnalyticsResponse
from app.services.analytics import AnalyticsService


router = APIRouter()


@router.get("/analytics", response_model=AnalyticsResponse)
async def get_analytics(session: AsyncSession = Depends(db_session_dep)) -> AnalyticsResponse:
    return AnalyticsResponse(**await AnalyticsService().build(session))
