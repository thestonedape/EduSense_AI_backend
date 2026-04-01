"""student persistence

Revision ID: 20260331_0003
Revises: 20260331_0002
Create Date: 2026-03-31 01:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260331_0003"
down_revision = "20260331_0002"
branch_labels = None
depends_on = None


student_lecture_status = postgresql.ENUM(
    "in_progress",
    "completed",
    name="studentlecturestatus",
    create_type=False,
)
chat_message_role = postgresql.ENUM(
    "user",
    "assistant",
    name="chatmessagerole",
    create_type=False,
)


def upgrade() -> None:
    student_lecture_status.create(op.get_bind(), checkfirst=True)
    chat_message_role.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "student_lecture_progress",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_email", sa.String(length=255), nullable=False),
        sa.Column("lecture_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", student_lecture_status, nullable=False),
        sa.Column("open_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("chat_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("quiz_attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("correct_quiz_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_opened_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["lecture_id"], ["lectures.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_email", "lecture_id", name="uq_student_lecture_progress"),
    )
    op.create_index(op.f("ix_student_lecture_progress_user_email"), "student_lecture_progress", ["user_email"], unique=False)
    op.create_index(op.f("ix_student_lecture_progress_lecture_id"), "student_lecture_progress", ["lecture_id"], unique=False)

    op.create_table(
        "student_chat_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_email", sa.String(length=255), nullable=False),
        sa.Column("lecture_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["lecture_id"], ["lectures.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_student_chat_sessions_user_email"), "student_chat_sessions", ["user_email"], unique=False)
    op.create_index(op.f("ix_student_chat_sessions_lecture_id"), "student_chat_sessions", ["lecture_id"], unique=False)

    op.create_table(
        "student_chat_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", chat_message_role, nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("citations", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["student_chat_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_student_chat_messages_session_id"), "student_chat_messages", ["session_id"], unique=False)

    op.create_table(
        "student_quiz_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_email", sa.String(length=255), nullable=False),
        sa.Column("lecture_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("question_id", sa.String(length=255), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("selected_answer", sa.Integer(), nullable=False),
        sa.Column("correct_answer", sa.Integer(), nullable=False),
        sa.Column("is_correct", sa.Boolean(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["lecture_id"], ["lectures.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_student_quiz_attempts_user_email"), "student_quiz_attempts", ["user_email"], unique=False)
    op.create_index(op.f("ix_student_quiz_attempts_lecture_id"), "student_quiz_attempts", ["lecture_id"], unique=False)
    op.create_index(op.f("ix_student_quiz_attempts_question_id"), "student_quiz_attempts", ["question_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_student_quiz_attempts_question_id"), table_name="student_quiz_attempts")
    op.drop_index(op.f("ix_student_quiz_attempts_lecture_id"), table_name="student_quiz_attempts")
    op.drop_index(op.f("ix_student_quiz_attempts_user_email"), table_name="student_quiz_attempts")
    op.drop_table("student_quiz_attempts")

    op.drop_index(op.f("ix_student_chat_messages_session_id"), table_name="student_chat_messages")
    op.drop_table("student_chat_messages")

    op.drop_index(op.f("ix_student_chat_sessions_lecture_id"), table_name="student_chat_sessions")
    op.drop_index(op.f("ix_student_chat_sessions_user_email"), table_name="student_chat_sessions")
    op.drop_table("student_chat_sessions")

    op.drop_index(op.f("ix_student_lecture_progress_lecture_id"), table_name="student_lecture_progress")
    op.drop_index(op.f("ix_student_lecture_progress_user_email"), table_name="student_lecture_progress")
    op.drop_table("student_lecture_progress")

    chat_message_role.drop(op.get_bind(), checkfirst=True)
    student_lecture_status.drop(op.get_bind(), checkfirst=True)
