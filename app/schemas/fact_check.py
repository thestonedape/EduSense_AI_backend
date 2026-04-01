from uuid import UUID

from pydantic import BaseModel, Field

from app.models.claim import ClaimStatus, ClaimVerdict
from app.schemas.common import ORMModel


class EvidenceResponse(ORMModel):
    id: UUID
    source_type: str
    source_reference: str
    excerpt: str
    similarity_score: float


class ClaimResponse(ORMModel):
    id: UUID
    sequence: int
    text: str
    verdict: ClaimVerdict
    confidence: float
    status: ClaimStatus
    source_excerpt: str | None
    rationale: str
    details: dict
    evidence_items: list[EvidenceResponse]


class FactCheckResponse(BaseModel):
    lecture_id: UUID
    lecture_name: str
    claims: list[ClaimResponse]


class FactCheckUpdateRequest(BaseModel):
    claim_id: UUID
    action: ClaimStatus | None = None
    edited_claim: str | None = None
    override_verdict: ClaimVerdict | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    rationale: str | None = None
