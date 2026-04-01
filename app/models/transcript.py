import uuid
from datetime import datetime
import enum

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class TopicValidationState(str, enum.Enum):
    pending_review = "pending_review"
    safe = "safe"
    flagged = "flagged"
    unclear = "unclear"


class TranscriptSegment(Base):
    __tablename__ = "transcript_segments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    lecture_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("lectures.id", ondelete="CASCADE"), nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    start_time: Mapped[float] = mapped_column(Float, nullable=False)
    end_time: Mapped[float] = mapped_column(Float, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    edited_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    lecture = relationship("Lecture", back_populates="transcript_segments")


class TopicSegment(Base):
    __tablename__ = "topic_segments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    lecture_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("lectures.id", ondelete="CASCADE"), nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    start_time: Mapped[float] = mapped_column(Float, nullable=False)
    end_time: Mapped[float] = mapped_column(Float, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    validation_state: Mapped[TopicValidationState] = mapped_column(
        Enum(TopicValidationState),
        default=TopicValidationState.pending_review,
        nullable=False,
    )
    approved_for_kb: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    validation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    claim_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    false_claim_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    lecture = relationship("Lecture", back_populates="topic_segments")
    claims = relationship("Claim", back_populates="topic")
