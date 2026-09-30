"""Close Supabase Data API access; application authorization runs in the backend."""
from alembic import op
import sqlalchemy as sa

revision = '20260930_0008'
down_revision = '20260930_0007'
branch_labels = depends_on = None
TABLES = (
    'alembic_version', 'lectures', 'lecture_content_items', 'processing_jobs',
    'job_outbox', 'submission_keys', 'reference_files', 'transcript_segments',
    'topic_segments', 'claims', 'claim_evidence', 'knowledge_chunks',
    'student_lecture_progress', 'student_chat_sessions', 'student_chat_messages',
    'student_quiz_attempts',
)

def upgrade():
    connection = op.get_bind()
    # No permissive browser policies should be inherited on backend-only tables.
    policies = connection.execute(sa.text(
        'SELECT count(*) FROM pg_policies WHERE schemaname = :schema AND tablename = ANY(:tables)'
    ), {'schema': 'public', 'tables': list(TABLES)}).scalar_one()
    if policies:
        raise RuntimeError('Review existing application-table policies before enabling backend-only RLS')
    for table in TABLES:
        op.execute(f'ALTER TABLE public."{table}" ENABLE ROW LEVEL SECURITY')

def downgrade():
    for table in TABLES:
        op.execute(f'ALTER TABLE public."{table}" DISABLE ROW LEVEL SECURITY')
