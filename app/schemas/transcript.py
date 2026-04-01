from datetime import date
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.schemas.lecture import ProcessingJobSnapshot
from app.schemas.common import ORMModel


class TranscriptSegmentResponse(ORMModel):
    id: UUID
    sequence: int
    start_time: float
    end_time: float
    text: str
    edited_text: str | None


class TopicSegmentResponse(ORMModel):
    id: UUID
    sequence: int
    title: str
    start_time: float
    end_time: float
    summary: str
    validation_state: str
    approved_for_kb: bool
    validation_reason: str | None
    claim_count: int
    false_claim_count: int
    transcript_segments: list[TranscriptSegmentResponse] = []


class ReferenceFileResponse(ORMModel):
    id: UUID
    original_filename: str
    file_type: str
    content_type: str | None
    created_at: datetime


class LectureContentItemResponse(ORMModel):
    id: UUID
    role: str
    original_filename: str
    file_type: str
    content_type: str | None
    created_at: datetime


class LectureDetailResponse(BaseModel):
    lecture_id: UUID
    lecture_name: str
    department_name: str | None
    program_name: str | None
    subject_name: str | None
    subject_code: str | None
    lecture_number: int | None
    lecture_date: date | None
    faculty_name: str | None
    original_filename: str
    course: str
    module: str
    status: str
    progress: int
    accuracy_score: float | None
    summary: str | None
    metrics: dict
    created_at: datetime
    updated_at: datetime
    latest_job: ProcessingJobSnapshot | None = None
    reference_files: list[ReferenceFileResponse]
    content_items: list[LectureContentItemResponse]
    transcript: list[TranscriptSegmentResponse]
    topics: list[TopicSegmentResponse]


class TranscriptSegmentUpdateRequest(BaseModel):
    text: str


class TopicSegmentUpdateRequest(BaseModel):
    title: str
    summary: str


class TopicApprovalRequest(BaseModel):
    approved_for_kb: bool
    reviewed_by: str | None = None
