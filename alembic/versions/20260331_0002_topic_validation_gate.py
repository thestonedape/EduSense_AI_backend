"""topic validation gate

Revision ID: 20260331_0002
Revises: 20260331_0001
Create Date: 2026-03-31 00:30:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260331_0002"
down_revision = "20260331_0001"
branch_labels = None
depends_on = None


topic_validation_state = postgresql.ENUM(
    "pending_review",
    "safe",
    "flagged",
    "unclear",
    name="topicvalidationstate",
)


def upgrade() -> None:
    topic_validation_state.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "topic_segments",
        sa.Column(
            "validation_state",
            topic_validation_state,
            nullable=False,
            server_default="pending_review",
        ),
    )
    op.add_column(
        "topic_segments",
        sa.Column("approved_for_kb", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("topic_segments", sa.Column("validation_reason", sa.Text(), nullable=True))
    op.add_column(
        "topic_segments",
        sa.Column("claim_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "topic_segments",
        sa.Column("false_claim_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("topic_segments", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("topic_segments", sa.Column("reviewed_by", sa.String(length=255), nullable=True))

    op.add_column("claims", sa.Column("topic_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_index(op.f("ix_claims_topic_id"), "claims", ["topic_id"], unique=False)
    op.create_foreign_key(
        "fk_claims_topic_id_topic_segments",
        "claims",
        "topic_segments",
        ["topic_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.alter_column("topic_segments", "validation_state", server_default=None)
    op.alter_column("topic_segments", "approved_for_kb", server_default=None)
    op.alter_column("topic_segments", "claim_count", server_default=None)
    op.alter_column("topic_segments", "false_claim_count", server_default=None)


def downgrade() -> None:
    op.drop_constraint("fk_claims_topic_id_topic_segments", "claims", type_="foreignkey")
    op.drop_index(op.f("ix_claims_topic_id"), table_name="claims")
    op.drop_column("claims", "topic_id")

    op.drop_column("topic_segments", "reviewed_by")
    op.drop_column("topic_segments", "reviewed_at")
    op.drop_column("topic_segments", "false_claim_count")
    op.drop_column("topic_segments", "claim_count")
    op.drop_column("topic_segments", "validation_reason")
    op.drop_column("topic_segments", "approved_for_kb")
    op.drop_column("topic_segments", "validation_state")

    topic_validation_state.drop(op.get_bind(), checkfirst=True)
