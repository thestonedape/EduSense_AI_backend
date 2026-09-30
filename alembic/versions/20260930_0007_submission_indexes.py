"""Scoped upload deduplication and bounded list indexes."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
revision = '20260930_0007'
down_revision = '20260930_0006'
branch_labels = depends_on = None

def upgrade():
    op.add_column('lectures', sa.Column('submission_fingerprint', sa.String(64), nullable=True))
    op.create_unique_constraint('uq_lecture_submission_fingerprint', 'lectures', ['submission_fingerprint'])
    op.create_table('submission_keys', sa.Column('key_hash', sa.String(64), primary_key=True), sa.Column('fingerprint', sa.String(64), nullable=False), sa.Column('lecture_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('lectures.id', ondelete='CASCADE'), nullable=False))
    op.create_index('ix_lecture_course_status_created', 'lectures', ['course','status','created_at'])
    op.create_index('ix_job_lecture_created', 'processing_jobs', ['lecture_id','created_at'])

def downgrade():
    op.drop_index('ix_job_lecture_created', 'processing_jobs')
    op.drop_index('ix_lecture_course_status_created', 'lectures')
    op.drop_table('submission_keys')
    op.drop_constraint('uq_lecture_submission_fingerprint', 'lectures', type_='unique')
    op.drop_column('lectures','submission_fingerprint')
