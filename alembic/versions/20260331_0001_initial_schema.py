"""initial schema

Revision ID: 20260331_0001
Revises:
Create Date: 2026-03-31 00:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "20260331_0001"
down_revision = None
branch_labels = None
depends_on = None


lecture_status = postgresql.ENUM("pending", "processing", "completed", "failed", name="lecturestatus", create_type=False)
claim_verdict = postgresql.ENUM("true", "false", "uncertain", name="claimverdict", create_type=False)
claim_status = postgresql.ENUM("pending", "approved", "rejected", "overridden", name="claimstatus", create_type=False)

lecture_status_create = postgresql.ENUM("pending", "processing", "completed", "failed", name="lecturestatus")
claim_verdict_create = postgresql.ENUM("true", "false", "uncertain", name="claimverdict")
claim_status_create = postgresql.ENUM("pending", "approved", "rejected", "overridden", name="claimstatus")


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    lecture_status_create.create(op.get_bind(), checkfirst=True)
    claim_verdict_create.create(op.get_bind(), checkfirst=True)
    claim_status_create.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "lectures",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lecture_name", sa.String(length=255), nullable=False),
        sa.Column("department_name", sa.String(length=255), nullable=True),
        sa.Column("program_name", sa.String(length=255), nullable=True),
        sa.Column("subject_name", sa.String(length=255), nullable=True),
        sa.Column("subject_code", sa.String(length=100), nullable=True),
        sa.Column("lecture_number", sa.Integer(), nullable=True),
        sa.Column("lecture_date", sa.Date(), nullable=True),
        sa.Column("faculty_name", sa.String(length=255), nullable=True),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("course", sa.String(length=255), nullable=False),
        sa.Column("module", sa.String(length=255), nullable=False),
        sa.Column("status", lecture_status, nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("accuracy_score", sa.Float(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_lectures_subject_code"), "lectures", ["subject_code"], unique=False)

    op.create_table(
        "claims",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lecture_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("verdict", claim_verdict, nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("status", claim_status, nullable=False),
        sa.Column("source_excerpt", sa.Text(), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["lecture_id"], ["lectures.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_claims_lecture_id"), "claims", ["lecture_id"], unique=False)

    op.create_table(
        "knowledge_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lecture_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("topic", sa.String(length=255), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("embedding", Vector(384), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["lecture_id"], ["lectures.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_knowledge_chunks_lecture_id"), "knowledge_chunks", ["lecture_id"], unique=False)
    op.create_index(op.f("ix_knowledge_chunks_topic"), "knowledge_chunks", ["topic"], unique=False)

    op.create_table(
        "reference_files",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lecture_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("file_type", sa.String(length=50), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["lecture_id"], ["lectures.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_reference_files_lecture_id"), "reference_files", ["lecture_id"], unique=False)

    op.create_table(
        "topic_segments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lecture_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("start_time", sa.Float(), nullable=False),
        sa.Column("end_time", sa.Float(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["lecture_id"], ["lectures.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_topic_segments_lecture_id"), "topic_segments", ["lecture_id"], unique=False)

    op.create_table(
        "transcript_segments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lecture_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("start_time", sa.Float(), nullable=False),
        sa.Column("end_time", sa.Float(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("edited_text", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["lecture_id"], ["lectures.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_transcript_segments_lecture_id"), "transcript_segments", ["lecture_id"], unique=False)

    op.create_table(
        "claim_evidence",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("claim_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_type", sa.String(length=50), nullable=False),
        sa.Column("source_reference", sa.String(length=255), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=False),
        sa.Column("similarity_score", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["claim_id"], ["claims.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_claim_evidence_claim_id"), "claim_evidence", ["claim_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_claim_evidence_claim_id"), table_name="claim_evidence")
    op.drop_table("claim_evidence")
    op.drop_index(op.f("ix_transcript_segments_lecture_id"), table_name="transcript_segments")
    op.drop_table("transcript_segments")
    op.drop_index(op.f("ix_topic_segments_lecture_id"), table_name="topic_segments")
    op.drop_table("topic_segments")
    op.drop_index(op.f("ix_reference_files_lecture_id"), table_name="reference_files")
    op.drop_table("reference_files")
    op.drop_index(op.f("ix_knowledge_chunks_topic"), table_name="knowledge_chunks")
    op.drop_index(op.f("ix_knowledge_chunks_lecture_id"), table_name="knowledge_chunks")
    op.drop_table("knowledge_chunks")
    op.drop_index(op.f("ix_claims_lecture_id"), table_name="claims")
    op.drop_table("claims")
    op.drop_index(op.f("ix_lectures_subject_code"), table_name="lectures")
    op.drop_table("lectures")

    claim_status_create.drop(op.get_bind(), checkfirst=True)
    claim_verdict_create.drop(op.get_bind(), checkfirst=True)
    lecture_status_create.drop(op.get_bind(), checkfirst=True)
