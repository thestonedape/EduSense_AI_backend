from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import db_session_dep
from app.schemas.lecture import DashboardStats
from app.services.dashboard import DashboardService


router = APIRouter()


@router.get("/dashboard", response_model=DashboardStats)
async def get_dashboard(session: AsyncSession = Depends(db_session_dep)) -> DashboardStats:
    return await DashboardService().get_stats(session)
