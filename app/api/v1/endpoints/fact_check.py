from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import db_session_dep
from app.models.claim import Claim, ClaimStatus
from app.models.lecture import Lecture
from app.schemas.fact_check import ClaimResponse, EvidenceResponse, FactCheckResponse, FactCheckUpdateRequest
from app.services.fact_check import FactCheckService


router = APIRouter()

SOURCE_EXCERPT_LIMIT = 700
EVIDENCE_EXCERPT_LIMIT = 400


def truncate_text(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1].rstrip()}..."


def serialize_claim(claim: Claim) -> ClaimResponse:
    return ClaimResponse(
        id=claim.id,
        sequence=claim.sequence,
        text=claim.text,
        verdict=claim.verdict,
        confidence=claim.confidence,
        status=claim.status,
        source_excerpt=truncate_text(claim.source_excerpt, SOURCE_EXCERPT_LIMIT),
        rationale=claim.rationale,
        details=claim.details,
        evidence_items=[
            EvidenceResponse(
                id=item.id,
                source_type=item.source_type,
                source_reference=item.source_reference,
                excerpt=truncate_text(item.excerpt, EVIDENCE_EXCERPT_LIMIT) or "",
                similarity_score=item.similarity_score,
            )
            for item in claim.evidence_items
        ],
    )


@router.get("/fact-check/{lecture_id}", response_model=FactCheckResponse)
async def get_fact_checks(lecture_id: UUID, session: AsyncSession = Depends(db_session_dep)) -> FactCheckResponse:
    lecture = await session.scalar(select(Lecture).where(Lecture.id == lecture_id))
    if lecture is None:
        raise HTTPException(status_code=404, detail="Lecture not found.")

    claims = await FactCheckService().get_claims_for_lecture(session, lecture_id)
    return FactCheckResponse(
        lecture_id=lecture.id,
        lecture_name=lecture.lecture_name,
        claims=[serialize_claim(item) for item in claims],
    )


@router.post("/fact-check/update", response_model=ClaimResponse)
async def update_fact_check(payload: FactCheckUpdateRequest, session: AsyncSession = Depends(db_session_dep)) -> ClaimResponse:
    stmt = select(Claim).where(Claim.id == payload.claim_id).options(selectinload(Claim.evidence_items))
    claim = await session.scalar(stmt)
    if claim is None:
        raise HTTPException(status_code=404, detail="Claim not found.")

    if payload.edited_claim:
        claim.text = payload.edited_claim
    if payload.override_verdict:
        claim.verdict = payload.override_verdict
        claim.status = ClaimStatus.overridden
    elif payload.action:
        claim.status = payload.action
    if payload.confidence is not None:
        claim.confidence = payload.confidence
    if payload.rationale:
        claim.rationale = payload.rationale

    await session.commit()
    refreshed_claim = await session.scalar(
        select(Claim).where(Claim.id == payload.claim_id).options(selectinload(Claim.evidence_items))
    )
    return serialize_claim(refreshed_claim)
