"""lecture content items and subject key

Revision ID: 20260331_0004
Revises: 20260331_0003
Create Date: 2026-03-31 01:20:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260331_0004"
down_revision = "20260331_0003"
branch_labels = None
depends_on = None


lecture_content_role = postgresql.ENUM(
    "lecture_source",
    "lecture_support",
    "reference_material",
    name="lecturecontentrole",
    create_type=False,
)


def upgrade() -> None:
    lecture_content_role.create(op.get_bind(), checkfirst=True)

    op.add_column("lectures", sa.Column("subject_key", sa.String(length=255), nullable=True))
    op.create_index(op.f("ix_lectures_subject_key"), "lectures", ["subject_key"], unique=False)

    op.create_table(
        "lecture_content_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lecture_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", lecture_content_role, nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("file_type", sa.String(length=50), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["lecture_id"], ["lectures.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_lecture_content_items_lecture_id"), "lecture_content_items", ["lecture_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_lecture_content_items_lecture_id"), table_name="lecture_content_items")
    op.drop_table("lecture_content_items")
    op.drop_index(op.f("ix_lectures_subject_key"), table_name="lectures")
    op.drop_column("lectures", "subject_key")
    lecture_content_role.drop(op.get_bind(), checkfirst=True)
