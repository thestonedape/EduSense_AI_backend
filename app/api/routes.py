from fastapi import APIRouter

from app.api.v1.endpoints import analytics, catalog, dashboard, fact_check, knowledge, lecture, processing, student, upload


router = APIRouter()
router.include_router(catalog.router, tags=["catalog"])
router.include_router(dashboard.router, tags=["dashboard"])
router.include_router(upload.router, tags=["upload"])
router.include_router(processing.router, tags=["processing"])
router.include_router(lecture.router, tags=["lecture"])
router.include_router(fact_check.router, tags=["fact-check"])
router.include_router(knowledge.router, tags=["knowledge"])
router.include_router(analytics.router, tags=["analytics"])
router.include_router(student.router, tags=["student"])
