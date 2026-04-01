from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import db_session_dep
from app.models.claim import Claim
from app.models.lecture_content import LectureContentItem
from app.models.lecture import Lecture
from app.models.processing_job import ProcessingJob
from app.models.reference_file import ReferenceFile
from app.models.transcript import TopicSegment, TranscriptSegment
from app.schemas.lecture import ProcessingJobSnapshot
from app.schemas.transcript import (
    LectureContentItemResponse,
    LectureDetailResponse,
    ReferenceFileResponse,
    TopicApprovalRequest,
    TopicSegmentResponse,
    TopicSegmentUpdateRequest,
    TranscriptSegmentResponse,
    TranscriptSegmentUpdateRequest,
)
from app.services.lecture_accuracy import derive_accuracy_score
from app.services.knowledge import KnowledgeService


router = APIRouter()
knowledge_service = KnowledgeService()


@router.get("/lecture/{lecture_id}", response_model=LectureDetailResponse)
async def get_lecture_detail(lecture_id: UUID, session: AsyncSession = Depends(db_session_dep)) -> LectureDetailResponse:
    stmt = (
        select(Lecture)
        .where(Lecture.id == lecture_id)
        .options(
            selectinload(Lecture.transcript_segments),
            selectinload(Lecture.topic_segments),
            selectinload(Lecture.reference_files),
            selectinload(Lecture.content_items),
        )
    )
    lecture = await session.scalar(stmt)
    if lecture is None:
        raise HTTPException(status_code=404, detail="Lecture not found.")

    transcript_rows = sorted(lecture.transcript_segments, key=lambda row: row.sequence)
    topic_rows = sorted(lecture.topic_segments, key=lambda row: row.sequence)
    transcript_payload = [TranscriptSegmentResponse.model_validate(item) for item in transcript_rows]

    topics_payload: list[TopicSegmentResponse] = []
    for index, topic in enumerate(topic_rows):
        next_topic = topic_rows[index + 1] if index + 1 < len(topic_rows) else None
        topic_segments = [
            TranscriptSegmentResponse.model_validate(segment)
            for segment in transcript_rows
            if (
                segment.start_time >= topic.start_time
                and (
                    next_topic is None
                    or segment.start_time < next_topic.start_time
                )
            )
        ]
        topics_payload.append(
            TopicSegmentResponse(
                id=topic.id,
                sequence=topic.sequence,
                title=topic.title,
                start_time=topic.start_time,
                end_time=topic.end_time,
                summary=topic.summary,
                validation_state=topic.validation_state.value,
                approved_for_kb=topic.approved_for_kb,
                validation_reason=topic.validation_reason,
                claim_count=topic.claim_count,
                false_claim_count=topic.false_claim_count,
                transcript_segments=topic_segments,
            )
        )

    claim_count = await session.scalar(
        select(func.count()).select_from(Claim).where(Claim.lecture_id == lecture.id)
    )
    metrics = lecture.metrics if isinstance(lecture.metrics, dict) else {}
    reference_payload = [
        ReferenceFileResponse.model_validate(item)
        for item in sorted(lecture.reference_files, key=lambda row: row.created_at)
    ]
    content_payload = [
        LectureContentItemResponse(
            id=item.id,
            role=item.role.value,
            original_filename=item.original_filename,
            file_type=item.file_type,
            content_type=item.content_type,
            created_at=item.created_at,
        )
        for item in sorted(lecture.content_items, key=lambda row: row.created_at)
    ]
    latest_job = await session.scalar(
        select(ProcessingJob)
        .where(ProcessingJob.lecture_id == lecture.id)
        .order_by(ProcessingJob.created_at.desc())
        .limit(1)
    )

    return LectureDetailResponse(
        lecture_id=lecture.id,
        lecture_name=lecture.lecture_name,
        department_name=lecture.department_name,
        program_name=lecture.program_name,
        subject_name=lecture.subject_name,
        subject_code=lecture.subject_code,
        lecture_number=lecture.lecture_number,
        lecture_date=lecture.lecture_date,
        faculty_name=lecture.faculty_name,
        original_filename=lecture.original_filename,
        course=lecture.course,
        module=lecture.module,
        status=lecture.status.value,
        progress=lecture.progress,
        accuracy_score=derive_accuracy_score(
            stored_accuracy=lecture.accuracy_score,
            metrics=metrics,
            claim_count=int(claim_count or 0),
            status=lecture.status,
        ),
        summary=lecture.summary,
        metrics={**metrics, "claims": int(claim_count or 0)},
        created_at=lecture.created_at,
        updated_at=lecture.updated_at,
        latest_job=ProcessingJobSnapshot.model_validate(latest_job) if latest_job is not None else None,
        reference_files=reference_payload,
        content_items=content_payload,
        transcript=transcript_payload,
        topics=topics_payload,
    )


@router.put("/lecture/transcript/{segment_id}", response_model=TranscriptSegmentResponse)
async def update_transcript_segment(
    segment_id: UUID,
    payload: TranscriptSegmentUpdateRequest,
    session: AsyncSession = Depends(db_session_dep),
) -> TranscriptSegmentResponse:
    segment = await session.scalar(select(TranscriptSegment).where(TranscriptSegment.id == segment_id))
    if segment is None:
        raise HTTPException(status_code=404, detail="Transcript segment not found.")

    segment.edited_text = payload.text
    await session.commit()
    await session.refresh(segment)
    return TranscriptSegmentResponse.model_validate(segment)


@router.put("/lecture/topic/{topic_id}", response_model=TopicSegmentResponse)
async def update_topic_segment(
    topic_id: UUID,
    payload: TopicSegmentUpdateRequest,
    session: AsyncSession = Depends(db_session_dep),
) -> TopicSegmentResponse:
    topic = await session.scalar(select(TopicSegment).where(TopicSegment.id == topic_id))
    if topic is None:
        raise HTTPException(status_code=404, detail="Topic segment not found.")

    topic.title = payload.title
    topic.summary = payload.summary
    await session.commit()
    await session.refresh(topic)
    return TopicSegmentResponse.model_validate(topic)


@router.post("/lecture/topic/{topic_id}/approval", response_model=TopicSegmentResponse)
async def update_topic_approval(
    topic_id: UUID,
    payload: TopicApprovalRequest,
    session: AsyncSession = Depends(db_session_dep),
) -> TopicSegmentResponse:
    topic = await session.scalar(select(TopicSegment).where(TopicSegment.id == topic_id))
    if topic is None:
        raise HTTPException(status_code=404, detail="Topic segment not found.")

    if payload.approved_for_kb and topic.validation_state.value != "safe":
        raise HTTPException(status_code=400, detail="Only safe topics can be approved for the student knowledge base.")

    was_approved = topic.approved_for_kb
    topic.approved_for_kb = payload.approved_for_kb
    if payload.approved_for_kb:
        topic.reviewed_at = datetime.now(timezone.utc)
        topic.reviewed_by = payload.reviewed_by or "admin"
    else:
        topic.reviewed_at = None
        topic.reviewed_by = None

    lecture = await session.scalar(select(Lecture).where(Lecture.id == topic.lecture_id))
    if lecture is not None:
        metrics = lecture.metrics if isinstance(lecture.metrics, dict) else {}
        approved_topics = await session.scalar(
            select(func.count()).select_from(TopicSegment).where(
                TopicSegment.lecture_id == topic.lecture_id,
                TopicSegment.approved_for_kb.is_(True),
            )
        )
        current_approved = int(approved_topics or 0)
        if payload.approved_for_kb and not was_approved:
            current_approved += 1
        elif not payload.approved_for_kb and was_approved:
            current_approved = max(current_approved - 1, 0)
        lecture.metrics = {
            **metrics,
            "approved_topics": current_approved,
        }

    await knowledge_service.sync_topic_visibility(session, topic)
    await session.commit()
    await session.refresh(topic)
    return TopicSegmentResponse.model_validate(topic)
