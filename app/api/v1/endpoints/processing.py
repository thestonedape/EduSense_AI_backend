from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import db_session_dep
from app.models.claim import Claim
from app.models.lecture import Lecture, LectureStatus
from app.models.processing_job import ProcessingJob, ProcessingJobType
from app.schemas.lecture import ProcessingItem, ProcessingJobSnapshot
from app.services.lecture_accuracy import derive_accuracy_score
from app.services.processing import ProcessingService


router = APIRouter()


async def get_latest_processing_job(session: AsyncSession, lecture_id: UUID) -> ProcessingJob | None:
    return await session.scalar(
        select(ProcessingJob)
        .where(ProcessingJob.lecture_id == lecture_id)
        .order_by(ProcessingJob.created_at.desc())
        .limit(1)
    )


def build_processing_item(
    lecture: Lecture,
    *,
    claim_count: int = 0,
    latest_job: ProcessingJob | None = None,
) -> ProcessingItem:
    metrics = lecture.metrics if isinstance(lecture.metrics, dict) else {}
    return ProcessingItem(
        id=lecture.id,
        lecture_name=lecture.lecture_name,
        department_name=lecture.department_name,
        program_name=lecture.program_name,
        subject_name=lecture.subject_name,
        subject_code=lecture.subject_code,
        lecture_number=lecture.lecture_number,
        lecture_date=lecture.lecture_date,
        faculty_name=lecture.faculty_name,
        course=lecture.course,
        module=lecture.module,
        status=lecture.status,
        progress=lecture.progress,
        accuracy_score=derive_accuracy_score(
            stored_accuracy=lecture.accuracy_score,
            metrics=metrics,
            claim_count=int(claim_count or 0),
            status=lecture.status,
        ),
        created_at=lecture.created_at,
        error_message=lecture.error_message,
        metrics={
            **metrics,
            "claims": int(claim_count or 0),
        },
        latest_job=ProcessingJobSnapshot.model_validate(latest_job) if latest_job is not None else None,
    )


async def to_processing_item(session: AsyncSession, lecture: Lecture) -> ProcessingItem:
    claim_count = await session.scalar(
        select(func.count()).select_from(Claim).where(Claim.lecture_id == lecture.id)
    )
    latest_job = await get_latest_processing_job(session, lecture.id)
    return build_processing_item(
        lecture,
        claim_count=int(claim_count or 0),
        latest_job=latest_job,
    )


@router.get("/processing", response_model=list[ProcessingItem])
async def list_processing_jobs(session: AsyncSession = Depends(db_session_dep)) -> list[ProcessingItem]:
    lectures = (await session.scalars(select(Lecture).order_by(Lecture.created_at.desc()))).all()
    if not lectures:
        return []

    lecture_ids = [lecture.id for lecture in lectures]
    claim_rows = (
        await session.execute(
            select(Claim.lecture_id, func.count().label("claim_count"))
            .where(Claim.lecture_id.in_(lecture_ids))
            .group_by(Claim.lecture_id)
        )
    ).all()
    claim_count_by_lecture = {
        lecture_id: int(claim_count or 0)
        for lecture_id, claim_count in claim_rows
    }

    jobs = (
        await session.scalars(
            select(ProcessingJob)
            .where(ProcessingJob.lecture_id.in_(lecture_ids))
            .order_by(ProcessingJob.lecture_id, ProcessingJob.created_at.desc())
        )
    ).all()
    latest_job_by_lecture: dict[UUID, ProcessingJob] = {}
    for job in jobs:
        latest_job_by_lecture.setdefault(job.lecture_id, job)

    return [
        build_processing_item(
            lecture,
            claim_count=claim_count_by_lecture.get(lecture.id, 0),
            latest_job=latest_job_by_lecture.get(lecture.id),
        )
        for lecture in lectures
    ]


@router.get("/processing/{lecture_id}", response_model=ProcessingItem)
async def get_processing_job(lecture_id: UUID, session: AsyncSession = Depends(db_session_dep)) -> ProcessingItem:
    lecture = await session.scalar(select(Lecture).where(Lecture.id == lecture_id))
    if lecture is None:
        raise HTTPException(status_code=404, detail="Lecture not found.")
    return await to_processing_item(session, lecture)


@router.post("/processing/{lecture_id}/rebuild-structure", response_model=ProcessingItem)
async def rebuild_processing_structure(
    background_tasks: BackgroundTasks,
    lecture_id: UUID,
    session: AsyncSession = Depends(db_session_dep),
) -> ProcessingItem:
    lecture = await session.scalar(select(Lecture).where(Lecture.id == lecture_id))
    if lecture is None:
        raise HTTPException(status_code=404, detail="Lecture not found.")

    if lecture.status == LectureStatus.processing:
        return await to_processing_item(session, lecture)

    lecture.status = LectureStatus.processing
    lecture.error_message = None
    lecture.progress = max(int(lecture.progress or 0), 55)
    metrics = lecture.metrics if isinstance(lecture.metrics, dict) else {}
    lecture.metrics = {**metrics, "rebuild_requested": True, "claims": 0}
    processing_service = ProcessingService()
    job = await processing_service.create_job(
        session,
        lecture.id,
        job_type=ProcessingJobType.rebuild_structure,
        details={"source": "admin_rebuild"},
    )
    await session.commit()
    await session.refresh(lecture)

    background_tasks.add_task(processing_service.launch_rebuild_structure, lecture_id, job.id)
    return await to_processing_item(session, lecture)


@router.post("/processing/{lecture_id}/resume", response_model=ProcessingItem)
async def resume_processing_job(
    background_tasks: BackgroundTasks,
    lecture_id: UUID,
    session: AsyncSession = Depends(db_session_dep),
) -> ProcessingItem:
    lecture = await session.scalar(select(Lecture).where(Lecture.id == lecture_id))
    if lecture is None:
        raise HTTPException(status_code=404, detail="Lecture not found.")

    processing_service = ProcessingService()
    job = await processing_service.resume_latest_job(session, lecture)
    await session.refresh(lecture)

    if job.job_type == ProcessingJobType.rebuild_structure:
        background_tasks.add_task(processing_service.launch_rebuild_structure, lecture_id, job.id)
    else:
        background_tasks.add_task(processing_service.launch_pipeline, lecture_id, job.id)

    return await to_processing_item(session, lecture)
