from fastapi import APIRouter

from app.services.catalog import ACADEMIC_CATALOG


router = APIRouter()


@router.get("/catalog")
async def get_catalog() -> list[dict]:
    return ACADEMIC_CATALOG
