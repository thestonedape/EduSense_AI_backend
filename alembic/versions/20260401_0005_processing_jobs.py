"""processing job tracking

Revision ID: 20260401_0005
Revises: 20260331_0004
Create Date: 2026-04-01 10:40:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260401_0005"
down_revision = "20260331_0004"
branch_labels = None
depends_on = None


processing_job_type = postgresql.ENUM(
    "upload_pipeline",
    "rebuild_structure",
    name="processingjobtype",
    create_type=False,
)

processing_job_status = postgresql.ENUM(
    "queued",
    "running",
    "completed",
    "failed",
    name="processingjobstatus",
    create_type=False,
)


def upgrade() -> None:
    processing_job_type.create(op.get_bind(), checkfirst=True)
    processing_job_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "processing_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lecture_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_type", processing_job_type, nullable=False),
        sa.Column("status", processing_job_status, nullable=False),
        sa.Column("stage", sa.String(length=100), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["lecture_id"], ["lectures.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_processing_jobs_job_type"), "processing_jobs", ["job_type"], unique=False)
    op.create_index(op.f("ix_processing_jobs_lecture_id"), "processing_jobs", ["lecture_id"], unique=False)
    op.create_index(op.f("ix_processing_jobs_status"), "processing_jobs", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_processing_jobs_status"), table_name="processing_jobs")
    op.drop_index(op.f("ix_processing_jobs_lecture_id"), table_name="processing_jobs")
    op.drop_index(op.f("ix_processing_jobs_job_type"), table_name="processing_jobs")
    op.drop_table("processing_jobs")
    processing_job_status.drop(op.get_bind(), checkfirst=True)
    processing_job_type.drop(op.get_bind(), checkfirst=True)
