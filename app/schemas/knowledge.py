from uuid import UUID

from pydantic import BaseModel

from app.schemas.common import ORMModel


class KnowledgeItem(ORMModel):
    id: UUID
    lecture_id: UUID
    topic: str
    content: str
    details: dict


class KnowledgeSearchResponse(BaseModel):
    query: str
    results: list[KnowledgeItem]
