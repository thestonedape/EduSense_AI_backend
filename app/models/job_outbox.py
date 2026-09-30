"""Transactional dispatch intent; PostgreSQL remains the recovery authority."""
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Integer, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base

class JobOutbox(Base):
    __tablename__ = 'job_outbox'
    job_id = mapped_column(UUID(as_uuid=True), ForeignKey('processing_jobs.id', ondelete='CASCADE'), primary_key=True)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dispatch_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
