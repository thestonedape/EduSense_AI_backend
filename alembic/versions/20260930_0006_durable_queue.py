"""durable queue leases and transactional outbox
Revision ID: 20260930_0006
Revises: 20260401_0005
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
revision='20260930_0006'
down_revision='20260401_0005'
branch_labels=None
depends_on=None

def upgrade():
    op.add_column('processing_jobs',sa.Column('attempts',sa.Integer(),nullable=False,server_default='0'))
    op.add_column('processing_jobs',sa.Column('lease_token',postgresql.UUID(as_uuid=True),nullable=True))
    op.add_column('processing_jobs',sa.Column('lease_expires_at',sa.DateTime(timezone=True),nullable=True))
    op.create_index('ix_processing_jobs_lease_expires_at','processing_jobs',['lease_expires_at'])
    op.create_table('job_outbox',sa.Column('job_id',postgresql.UUID(as_uuid=True),sa.ForeignKey('processing_jobs.id',ondelete='CASCADE'),primary_key=True),sa.Column('next_attempt_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()),sa.Column('delivered_at',sa.DateTime(timezone=True),nullable=True),sa.Column('dispatch_attempts',sa.Integer(),nullable=False,server_default='0'))
    op.create_index('ix_job_outbox_next_attempt_at','job_outbox',['next_attempt_at'])

def downgrade():
    op.drop_table('job_outbox')
    op.drop_index('ix_processing_jobs_lease_expires_at',table_name='processing_jobs')
    for name in ['lease_expires_at','lease_token','attempts']:
        op.drop_column('processing_jobs',name)
