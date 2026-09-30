"""100-job container benchmark of the ledger/queue with fixture providers.

Requires migrated, disposable *_test PostgreSQL and Redis. Does not benchmark
uploads, authentication, Deepgram, RAG or Q&A. Never use a live database.
"""
import argparse
import asyncio
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def docker(*args):
    return subprocess.check_output(['docker', *args], text=True).strip()


def percentile(values, fraction):
    return sorted(values)[max(0, int(len(values) * fraction + .999) - 1)] if values else None


async def benchmark(args):
    from sqlalchemy import delete, func, select
    from app.db.session import SessionLocal, engine
    from app.models.lecture import Lecture
    from app.models.processing_job import ProcessingJob, ProcessingJobType
    from app.models.transcript import TranscriptSegment
    from app.services.processing import ProcessingService
    import httpx

    assert engine.url.database.endswith('_test'), 'Disposable *_test database required'
    assert args.container_database.rsplit('/', 1)[-1].endswith('_test')
    assert args.requests >= 100
    name = 'sde-edusense-ledger-benchmark'
    ids, jobs, acknowledgements, raw = [], [], [], []
    command = "import subprocess; subprocess.Popen(['python','scripts/chaos_worker.py']); subprocess.call(['python','-m','uvicorn','app.main:app','--host','0.0.0.0','--port','8000'])"
    started = time.perf_counter()
    docker('run', '-d', '--name', name, '--memory', '512m', '--memory-swap', '512m',
           '-p', '8014:8000', '-e', f'DATABASE_URL={args.container_database}',
           '-e', f'REDIS_URL={args.container_redis}', '-e', 'AUTO_BOOTSTRAP_SCHEMA=false',
           '-e', 'STORAGE_BACKEND=local', '-e', 'ENVIRONMENT=development',
           '-e', 'INTERNAL_API_KEY=benchmark-only', '-e', 'CHAOS_CALL_LOG=/tmp/calls.log',
           '-e', 'CHAOS_STAGE_SECONDS=0.01', args.image, 'python', '-c', command)
    cold = None
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            for _ in range(120):
                try:
                    if (await client.get('http://localhost:8014/ready')).status_code == 200:
                        cold = time.perf_counter() - started
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(.5)
        if cold is None:
            raise RuntimeError('Fixture service did not become ready')
        accepted = time.perf_counter()
        for n in range(args.requests):
            before = time.perf_counter()
            async with SessionLocal() as db:
                lecture = Lecture(lecture_name=f'benchmark-{n}', original_filename='fixture.wav',
                                  storage_path='unused-fixture', course='benchmark', module='fixture', metrics={})
                db.add(lecture)
                await db.flush()
                ids.append(lecture.id)
                job = await ProcessingService().create_job(db, lecture.id, ProcessingJobType.upload_pipeline)
                jobs.append(job.id)
                await db.commit()
            acknowledgements.append(time.perf_counter() - before)
        for _ in range(1200):
            async with SessionLocal() as db:
                rows = (await db.scalars(select(ProcessingJob).where(ProcessingJob.id.in_(jobs)))).all()
                if all(job.status.value in ('completed', 'failed') for job in rows):
                    for job in rows:
                        segments = await db.scalar(select(func.count()).select_from(TranscriptSegment).where(TranscriptSegment.lecture_id == job.lecture_id))
                        raw.append({'job_id': str(job.id), 'status': job.status.value, 'transcript_rows': segments,
                                    'queue_wait_seconds': (job.started_at - job.created_at).total_seconds() if job.started_at else None,
                                    'end_to_end_seconds': (job.finished_at - job.created_at).total_seconds() if job.finished_at else None})
                    break
            await asyncio.sleep(.25)
        elapsed = time.perf_counter() - accepted
        peak = int(docker('exec', name, 'cat', '/sys/fs/cgroup/memory.peak'))
        events = dict(line.split() for line in docker('exec', name, 'cat', '/sys/fs/cgroup/memory.events').splitlines())
        state = json.loads(docker('inspect', '-f', '{{json .State}}', name))
        latencies = [row['end_to_end_seconds'] for row in raw if row['end_to_end_seconds'] is not None]
        report = {'scope': '512 MB API + arq container; real PostgreSQL/Redis; deterministic provider fixtures; ledger submissions, not uploads or actual transcription/RAG',
                  'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                  'revision': docker('image', 'inspect', '-f', '{{.Id}}', args.image),
                  'image_bytes': int(docker('image', 'inspect', '-f', '{{.Size}}', args.image)),
                  'memory_limit': '512m', 'requests': args.requests, 'completed': sum(row['status'] == 'completed' for row in raw),
                  'failures_or_pending': args.requests - sum(row['status'] == 'completed' for row in raw),
                  'rows_not_exactly_one': sum(row['transcript_rows'] != 1 for row in raw),
                  'cold_start_to_ready_seconds': cold, 'ledger_commit_p50_seconds': statistics.median(acknowledgements),
                  'ledger_commit_p95_seconds': percentile(acknowledgements, .95),
                  'end_to_end_p50_seconds': percentile(latencies, .5), 'end_to_end_p95_seconds': percentile(latencies, .95),
                  'completed_jobs_per_second': len(raw) / elapsed, 'peak_cgroup_memory_bytes': peak,
                  'oom_kill_events': int(events['oom_kill']), 'container_oom_killed': state['OOMKilled'],
                  'raw_commit_seconds': acknowledgements, 'raw_jobs': raw}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2))
        print(json.dumps({key: value for key, value in report.items() if not key.startswith('raw_')}, indent=2))
    finally:
        docker('rm', '-f', name)
        async with SessionLocal() as db:
            await db.execute(delete(Lecture).where(Lecture.id.in_(ids)))
            await db.commit()
        await engine.dispose()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', default='sde/edusense:review')
    parser.add_argument('--requests', type=int, default=100)
    parser.add_argument('--container-database', required=True)
    parser.add_argument('--container-redis', required=True)
    parser.add_argument('--output', type=Path, default=Path('artifacts/ledger-container-512m.json'))
    asyncio.run(benchmark(parser.parse_args()))
