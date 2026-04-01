from datetime import date
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.models.lecture import LectureStatus
from app.models.processing_job import ProcessingJobStatus, ProcessingJobType
from app.schemas.common import ORMModel


class LectureSummary(ORMModel):
    id: UUID
    lecture_name: str
    department_name: str | None
    program_name: str | None
    subject_name: str | None
    subject_code: str | None
    lecture_number: int | None
    lecture_date: date | None
    faculty_name: str | None
    course: str
    module: str
    status: LectureStatus
    progress: int
    accuracy_score: float | None
    created_at: datetime


class ProcessingItem(LectureSummary):
    error_message: str | None
    metrics: dict
    latest_job: "ProcessingJobSnapshot | None" = None


class ProcessingJobSnapshot(ORMModel):
    id: UUID
    job_type: ProcessingJobType
    status: ProcessingJobStatus
    stage: str
    retry_count: int
    error_message: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    last_heartbeat_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class DashboardStats(BaseModel):
    total_lectures_processed: int
    lectures_in_queue: int
    failed_jobs: int
    accuracy_overview: float
    approved_topics_total: int = 0
    flagged_topics_total: int = 0
    lectures_blocked_from_kb: int = 0
    reference_backed_lectures: int = 0
    model_reviewed_lectures: int = 0
    active_processing_jobs: int = 0
    average_job_duration_minutes: float = 0
    average_job_retries: float = 0
    status_breakdown: dict[str, int]
    recent_lectures: list[LectureSummary]


class UploadResponse(BaseModel):
    lecture_id: UUID
    lecture_name: str
    status: LectureStatus
    message: str
    processing_job_id: UUID | None = None
    reference_file_count: int = 0
    content_item_count: int = 0
