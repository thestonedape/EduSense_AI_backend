"""Private PostgreSQL backup and sanitized pre-migration audit. Never publish the dump."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import create_engine, text
from app.core.config import get_settings

parser = argparse.ArgumentParser()
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
engine = create_engine(get_settings().sync_database_url)
with engine.connect() as connection:
    role = connection.execute(text('SELECT current_user, rolsuper, rolbypassrls FROM pg_roles WHERE rolname=current_user')).one()
    audit = {'checked_utc': datetime.now(timezone.utc).isoformat(), 'database_role_is_privileged': bool(role[1] or role[2]),
        'migration': connection.scalar(text('SELECT version_num FROM public.alembic_version')),
        'lectures': connection.scalar(text('SELECT count(*) FROM public.lectures')),
        'knowledge_chunks': connection.scalar(text('SELECT count(*) FROM public.knowledge_chunks')),
        'application_policies': connection.scalar(text("SELECT count(*) FROM pg_policies WHERE schemaname='public'")),
        'tables': [dict(row) for row in connection.execute(text("SELECT tablename, rowsecurity, tableowner=current_user AS serving_role_owns_table FROM pg_tables WHERE schemaname='public' ORDER BY tablename")).mappings()]}
url = engine.url
environment = os.environ.copy()
environment['PGPASSWORD'] = url.password
environment['PGSSLMODE'] = 'require'
args.output.parent.mkdir(parents=True, exist_ok=True)
with args.output.open('wb') as destination:
    result = subprocess.run(['docker', 'run', '--rm', '--env', 'PGPASSWORD', '--env', 'PGSSLMODE',
        'pgvector/pgvector:pg17', 'pg_dump', '--host', url.host, '--port', str(url.port or 5432),
        '--username', url.username, '--dbname', url.database, '--format=custom', '--no-owner', '--no-acl', '--schema=public'],
        env=environment, stdout=destination, stderr=subprocess.PIPE)
if result.returncode:
    raise SystemExit('Backup failed; credentials and database output are withheld')
with args.output.open('rb') as source:
    result = subprocess.run(['docker', 'run', '--rm', '-i', 'pgvector/pgvector:pg17', 'pg_restore', '--list'],
        stdin=source, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
if result.returncode:
    raise SystemExit('Backup archive validation failed')
audit.update({'backup_bytes': args.output.stat().st_size, 'backup_sha256': hashlib.sha256(args.output.read_bytes()).hexdigest(), 'archive_list_verified': True})
args.output.with_suffix('.json').write_text(json.dumps(audit, indent=2))
print(json.dumps(audit, indent=2))
