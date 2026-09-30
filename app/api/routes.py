from fastapi import APIRouter, Depends
from app.api.deps import admin_dep, trusted_student_email_dep

from app.api.v1.endpoints import analytics, catalog, dashboard, fact_check, knowledge, lecture, processing, student, upload


router = APIRouter()
router.include_router(catalog.router, tags=["catalog"], dependencies=[Depends(admin_dep)])
router.include_router(dashboard.router, tags=["dashboard"], dependencies=[Depends(admin_dep)])
router.include_router(upload.router, tags=["upload"], dependencies=[Depends(admin_dep)])
router.include_router(processing.router, tags=["processing"], dependencies=[Depends(admin_dep)])
router.include_router(lecture.router, tags=["lecture"], dependencies=[Depends(admin_dep)])
router.include_router(fact_check.router, tags=["fact-check"], dependencies=[Depends(admin_dep)])
router.include_router(knowledge.router, tags=["knowledge"], dependencies=[Depends(admin_dep)])
router.include_router(analytics.router, tags=["analytics"], dependencies=[Depends(admin_dep)])
router.include_router(student.router, tags=["student"], dependencies=[Depends(trusted_student_email_dep)])
