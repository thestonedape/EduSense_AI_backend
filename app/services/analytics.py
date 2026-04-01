from collections import Counter, defaultdict

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.claim import Claim, ClaimVerdict
from app.models.knowledge import KnowledgeChunk
from app.models.lecture import Lecture
from app.models.processing_job import ProcessingJob, ProcessingJobStatus
from app.services.lecture_accuracy import derive_accuracy_score


class AnalyticsService:
    async def build(self, session: AsyncSession) -> dict:
        lectures = (await session.scalars(select(Lecture))).all()
        claim_rows = (
            await session.execute(select(Claim.lecture_id, func.count()).group_by(Claim.lecture_id))
        ).all()
        claim_count_map = {lecture_id: int(count) for lecture_id, count in claim_rows}
        low_accuracy = [
            {
                "lecture_name": lecture.lecture_name,
                "subject_code": lecture.subject_code,
                "subject_name": lecture.subject_name,
                "accuracy_score": derive_accuracy_score(
                    stored_accuracy=lecture.accuracy_score,
                    metrics=lecture.metrics,
                    claim_count=claim_count_map.get(lecture.id, 0),
                    status=lecture.status,
                ),
            }
            for lecture in lectures
        ]
        low_accuracy = [item for item in low_accuracy if item["accuracy_score"] is not None]
        low_accuracy.sort(key=lambda item: item["accuracy_score"])
        low_accuracy = low_accuracy[:5]

        claims = (await session.execute(select(Claim.verdict, Claim.text))).all()
        topic_counter = Counter()
        for verdict, text in claims:
            if verdict == ClaimVerdict.false:
                topic_counter.update(word.lower() for word in text.split()[:3])

        validation_overview = [
            {
                "label": "Approved Topics",
                "value": sum(int((lecture.metrics or {}).get("approved_topics", 0) or 0) for lecture in lectures),
            },
            {
                "label": "Flagged Topics",
                "value": sum(int((lecture.metrics or {}).get("flagged_topics", 0) or 0) for lecture in lectures),
            },
            {
                "label": "Unclear Topics",
                "value": sum(int((lecture.metrics or {}).get("unclear_topics", 0) or 0) for lecture in lectures),
            },
            {
                "label": "Blocked Lectures",
                "value": sum(
                    1
                    for lecture in lectures
                    if int((lecture.metrics or {}).get("topics", 0) or 0) > int((lecture.metrics or {}).get("approved_topics", 0) or 0)
                ),
            },
        ]

        coverage_rows = (await session.execute(select(KnowledgeChunk.topic, func.count(KnowledgeChunk.id)).group_by(KnowledgeChunk.topic))).all()
        coverage_gaps = [{"topic": topic, "chunk_count": count} for topic, count in coverage_rows if count < 3][:10]
        jobs = (await session.scalars(select(ProcessingJob))).all()
        pipeline_health_counter = Counter(job.status.value for job in jobs)
        pipeline_health = [
            {"label": label.replace("_", " "), "value": value}
            for label, value in pipeline_health_counter.items()
        ]
        latency_by_type: dict[str, list[float]] = defaultdict(list)
        stage_failure_counter = Counter()
        retry_hotspots = []
        for job in jobs:
            if job.started_at is not None and job.finished_at is not None:
                latency_by_type[job.job_type.value].append(
                    round((job.finished_at - job.started_at).total_seconds() / 60, 2)
                )
            if job.status == ProcessingJobStatus.failed:
                stage_failure_counter.update([job.stage or "unknown"])
            if job.retry_count > 0:
                retry_hotspots.append(
                    {
                        "lecture_id": str(job.lecture_id),
                        "job_type": job.job_type.value,
                        "stage": job.stage,
                        "retry_count": job.retry_count,
                    }
                )
        processing_latency = [
            {
                "label": job_type.replace("_", " "),
                "value": round(sum(values) / len(values), 2),
            }
            for job_type, values in latency_by_type.items()
            if values
        ]
        stage_failure_breakdown = [
            {"label": label.replace("_", " "), "value": value}
            for label, value in stage_failure_counter.most_common(8)
        ]
        lecture_name_map = {str(lecture.id): lecture.lecture_name for lecture in lectures}
        retry_hotspots = sorted(retry_hotspots, key=lambda item: item["retry_count"], reverse=True)[:10]
        retry_hotspots = [
            {
                "lecture_name": lecture_name_map.get(item["lecture_id"], "Lecture"),
                "job_type": item["job_type"],
                "stage": item["stage"],
                "retry_count": item["retry_count"],
            }
            for item in retry_hotspots
        ]

        blocked_lectures = []
        for lecture in lectures:
            metrics = lecture.metrics if isinstance(lecture.metrics, dict) else {}
            topics = int(metrics.get("topics", 0) or 0)
            approved = int(metrics.get("approved_topics", 0) or 0)
            flagged = int(metrics.get("flagged_topics", 0) or 0)
            if topics <= approved:
                continue
            blocked_lectures.append(
                {
                    "lecture_name": lecture.lecture_name,
                    "approved_topics": approved,
                    "blocked_topics": max(topics - approved, 0),
                    "flagged_topics": flagged,
                }
            )
        blocked_lectures = sorted(
            blocked_lectures,
            key=lambda item: (item["blocked_topics"], item["flagged_topics"]),
            reverse=True,
        )[:10]

        validation_source_counter = Counter(
            str((lecture.metrics or {}).get("fact_check_validation_source", "unknown"))
            for lecture in lectures
            if lecture.status.value == "completed"
        )
        validation_source_split = [
            {"label": label.replace("_", " "), "value": value}
            for label, value in validation_source_counter.items()
        ]

        trend_rows = (await session.execute(select(Lecture.created_at, Lecture.status).order_by(Lecture.created_at.asc()))).all()
        trend_map = defaultdict(lambda: {"completed": 0, "failed": 0, "processing": 0, "pending": 0})
        for created_at, status in trend_rows:
            trend_map[created_at.date().isoformat()][status.value] += 1

        return {
            "validation_overview": validation_overview,
            "pipeline_health": pipeline_health,
            "processing_latency": processing_latency,
            "stage_failure_breakdown": stage_failure_breakdown,
            "retry_hotspots": retry_hotspots,
            "lowest_accuracy_lectures": low_accuracy,
            "most_incorrect_topics": [{"topic": topic, "count": count} for topic, count in topic_counter.most_common(10)],
            "lectures_blocked_from_kb": blocked_lectures,
            "validation_source_split": validation_source_split,
            "coverage_gaps": coverage_gaps,
            "trends": [{"date": date, **counts} for date, counts in sorted(trend_map.items())],
        }
