from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import db_session_dep
from app.schemas.knowledge import KnowledgeItem, KnowledgeSearchResponse
from app.services.knowledge import KnowledgeService


router = APIRouter()


@router.get("/knowledge", response_model=KnowledgeSearchResponse)
async def search_knowledge(
    query: str = Query(..., min_length=2),
    topic: str | None = Query(default=None),
    limit: int = Query(default=10, ge=1, le=50),
    approved_only: bool = Query(default=True),
    session: AsyncSession = Depends(db_session_dep),
) -> KnowledgeSearchResponse:
    results = await KnowledgeService().search(
        session,
        query=query,
        topic=topic,
        limit=limit,
        approved_only=approved_only,
    )
    return KnowledgeSearchResponse(
        query=query,
        results=[KnowledgeItem.model_validate(item) for item in results],
    )
