import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class StudentLectureStatus(str, enum.Enum):
    in_progress = "in_progress"
    completed = "completed"


class ChatMessageRole(str, enum.Enum):
    user = "user"
    assistant = "assistant"


class StudentLectureProgress(Base):
    __tablename__ = "student_lecture_progress"
    __table_args__ = (UniqueConstraint("user_email", "lecture_id", name="uq_student_lecture_progress"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    lecture_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("lectures.id", ondelete="CASCADE"), nullable=False, index=True)
    status: Mapped[StudentLectureStatus] = mapped_column(Enum(StudentLectureStatus), default=StudentLectureStatus.in_progress, nullable=False)
    open_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    chat_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    quiz_attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    correct_quiz_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    lecture = relationship("Lecture")


class StudentChatSession(Base):
    __tablename__ = "student_chat_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    lecture_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("lectures.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    lecture = relationship("Lecture")
    messages = relationship("StudentChatMessage", back_populates="session", cascade="all, delete-orphan")


class StudentChatMessage(Base):
    __tablename__ = "student_chat_messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("student_chat_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    role: Mapped[ChatMessageRole] = mapped_column(Enum(ChatMessageRole), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    citations: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    session = relationship("StudentChatSession", back_populates="messages")


class StudentQuizAttempt(Base):
    __tablename__ = "student_quiz_attempts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    lecture_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("lectures.id", ondelete="CASCADE"), nullable=False, index=True)
    question_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    selected_answer: Mapped[int] = mapped_column(Integer, nullable=False)
    correct_answer: Mapped[int] = mapped_column(Integer, nullable=False)
    is_correct: Mapped[bool] = mapped_column(Boolean, nullable=False)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    lecture = relationship("Lecture")
