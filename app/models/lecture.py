import enum
import uuid
from datetime import datetime
from datetime import date

from sqlalchemy import Date, DateTime, Enum, Float, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class LectureStatus(str, enum.Enum):
    pending = "pending"
    processing = "processing"
    completed = "completed"
    failed = "failed"


class Lecture(Base):
    __tablename__ = "lectures"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    lecture_name: Mapped[str] = mapped_column(String(255), nullable=False)
    department_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    program_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subject_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subject_code: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    subject_key: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    lecture_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lecture_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    faculty_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    course: Mapped[str] = mapped_column(String(255), nullable=False)
    module: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[LectureStatus] = mapped_column(Enum(LectureStatus), default=LectureStatus.pending, nullable=False)
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    accuracy_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    metrics: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    transcript_segments = relationship("TranscriptSegment", back_populates="lecture", cascade="all, delete-orphan")
    topic_segments = relationship("TopicSegment", back_populates="lecture", cascade="all, delete-orphan")
    claims = relationship("Claim", back_populates="lecture", cascade="all, delete-orphan")
    knowledge_chunks = relationship("KnowledgeChunk", back_populates="lecture", cascade="all, delete-orphan")
    reference_files = relationship("ReferenceFile", back_populates="lecture", cascade="all, delete-orphan")
    content_items = relationship("LectureContentItem", back_populates="lecture", cascade="all, delete-orphan")
    processing_jobs = relationship("ProcessingJob", back_populates="lecture", cascade="all, delete-orphan")
