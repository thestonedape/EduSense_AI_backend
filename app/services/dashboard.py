from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.claim import Claim
from app.models.lecture import Lecture, LectureStatus
from app.models.processing_job import ProcessingJob, ProcessingJobStatus
from app.schemas.lecture import DashboardStats, LectureSummary
from app.services.lecture_accuracy import derive_accuracy_score


class DashboardService:
    async def get_stats(self, session: AsyncSession) -> DashboardStats:
        total_processed = await session.scalar(select(func.count()).select_from(Lecture).where(Lecture.status == LectureStatus.completed))
        in_queue = await session.scalar(select(func.count()).select_from(Lecture).where(Lecture.status.in_([LectureStatus.pending, LectureStatus.processing])))
        failed_jobs = await session.scalar(select(func.count()).select_from(Lecture).where(Lecture.status == LectureStatus.failed))
        breakdown_rows = (await session.execute(select(Lecture.status, func.count()).group_by(Lecture.status))).all()
        recent_rows = (await session.scalars(select(Lecture).order_by(Lecture.created_at.desc()).limit(8))).all()
        claim_rows = (
            await session.execute(select(Claim.lecture_id, func.count()).group_by(Claim.lecture_id))
        ).all()
        claim_count_map = {lecture_id: int(count) for lecture_id, count in claim_rows}
        derived_scores = [
            derive_accuracy_score(
                stored_accuracy=item.accuracy_score,
                metrics=item.metrics,
                claim_count=claim_count_map.get(item.id, 0),
                status=item.status,
            )
            for item in recent_rows
        ]
        all_rows = (await session.scalars(select(Lecture))).all()
        all_claim_rows = (
            await session.execute(select(Claim.lecture_id, func.count()).group_by(Claim.lecture_id))
        ).all()
        all_claim_count_map = {lecture_id: int(count) for lecture_id, count in all_claim_rows}
        all_scores = [
            derive_accuracy_score(
                stored_accuracy=item.accuracy_score,
                metrics=item.metrics,
                claim_count=all_claim_count_map.get(item.id, 0),
                status=item.status,
            )
            for item in all_rows
        ]
        recent_score_values = [score for score in all_scores if score is not None]
        avg_accuracy = round(sum(recent_score_values) / len(recent_score_values), 2) if recent_score_values else 0.0
        approved_topics_total = sum(int((item.metrics or {}).get("approved_topics", 0) or 0) for item in all_rows)
        flagged_topics_total = sum(int((item.metrics or {}).get("flagged_topics", 0) or 0) for item in all_rows)
        lectures_blocked_from_kb = sum(
            1
            for item in all_rows
            if int((item.metrics or {}).get("topics", 0) or 0) > int((item.metrics or {}).get("approved_topics", 0) or 0)
        )
        reference_backed_lectures = sum(
            1
            for item in all_rows
            if str((item.metrics or {}).get("fact_check_validation_source", "")) == "reference_evidence"
        )
        model_reviewed_lectures = sum(
            1
            for item in all_rows
            if str((item.metrics or {}).get("fact_check_validation_source", "")) == "model_knowledge"
        )
        jobs = (await session.scalars(select(ProcessingJob))).all()
        active_processing_jobs = sum(
            1 for job in jobs if job.status in {ProcessingJobStatus.queued, ProcessingJobStatus.running}
        )
        completed_durations = [
            round((job.finished_at - job.started_at).total_seconds() / 60, 2)
            for job in jobs
            if job.started_at is not None and job.finished_at is not None
        ]
        average_job_duration_minutes = (
            round(sum(completed_durations) / len(completed_durations), 2) if completed_durations else 0.0
        )
        average_job_retries = round(sum(job.retry_count for job in jobs) / len(jobs), 2) if jobs else 0.0

        return DashboardStats(
            total_lectures_processed=total_processed or 0,
            lectures_in_queue=in_queue or 0,
            failed_jobs=failed_jobs or 0,
            accuracy_overview=avg_accuracy,
            approved_topics_total=approved_topics_total,
            flagged_topics_total=flagged_topics_total,
            lectures_blocked_from_kb=lectures_blocked_from_kb,
            reference_backed_lectures=reference_backed_lectures,
            model_reviewed_lectures=model_reviewed_lectures,
            active_processing_jobs=active_processing_jobs,
            average_job_duration_minutes=average_job_duration_minutes,
            average_job_retries=average_job_retries,
            status_breakdown={status.value: count for status, count in breakdown_rows},
            recent_lectures=[LectureSummary.model_validate(item) for item in recent_rows],
        )
