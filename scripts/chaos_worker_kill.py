"""Hard-kill a real arq worker process mid-job and verify every job still finishes once.

Usage (isolated *_test database and disposable Redis only):
    python scripts/chaos_worker_kill.py --jobs 10 --kills 10 --output artifacts/chaos-worker-kill.json
"""
import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def start_worker(env):
    return subprocess.Popen([sys.executable, str(ROOT / 'scripts' / 'chaos_worker.py')], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


async def main(args):
    if not os.environ['DATABASE_URL'].rsplit('/', 1)[-1].endswith('_test'):
        raise SystemExit('requires a *_test database')
    from sqlalchemy import delete, func, select
    from app.db.session import SessionLocal, engine
    from app.models.lecture import Lecture
    from app.models.processing_job import ProcessingJob, ProcessingJobStatus, ProcessingJobType
    from app.models.transcript import TranscriptSegment
    from app.services.processing import ProcessingService

    fd, name = tempfile.mkstemp(suffix='.log')
    os.close(fd)
    calls = Path(name)
    env = {**os.environ, 'CHAOS_CALL_LOG': str(calls)}
    ids, jobs = [], []
    async with SessionLocal() as db:
        for n in range(args.jobs):
            lecture = Lecture(lecture_name=f'chaos-{n}', original_filename='fixture.wav', storage_path='unused-fixture',
                              course='chaos', module='chaos', metrics={})
            db.add(lecture)
            await db.flush()
            job = await ProcessingService().create_job(db, lecture.id, ProcessingJobType.upload_pipeline)
            ids.append(lecture.id)
            jobs.append(job.id)
        await db.commit()

    killed, kill_log = set(), []
    started = time.monotonic()
    worker = start_worker(env)
    try:
        while len(kill_log) < args.kills and time.monotonic() - started < args.timeout:
            async with SessionLocal() as db:
                running = (await db.scalars(select(ProcessingJob).where(
                    ProcessingJob.id.in_(jobs), ProcessingJob.status == ProcessingJobStatus.running))).all()
            target = next((j for j in running if j.id not in killed and (j.details or {}).get('transcription_checkpoint')), None)
            if target is None:
                await asyncio.sleep(0.2)
                continue
            worker.kill()  # TerminateProcess / SIGKILL: no cleanup code runs
            worker.wait()
            killed.add(target.id)
            kill_log.append({'job_id': str(target.id), 'attempt': target.attempts,
                             'at': datetime.now(timezone.utc).isoformat()})
            worker = start_worker(env)

        while time.monotonic() - started < args.timeout:
            async with SessionLocal() as db:
                pending = await db.scalar(select(func.count()).select_from(ProcessingJob).where(
                    ProcessingJob.id.in_(jobs),
                    ProcessingJob.status.notin_([ProcessingJobStatus.completed, ProcessingJobStatus.failed])))
            if pending == 0:
                break
            await asyncio.sleep(1)
        elapsed = time.monotonic() - started
    finally:
        worker.kill()
        worker.wait()

    async with SessionLocal() as db:
        rows = (await db.scalars(select(ProcessingJob).where(ProcessingJob.id.in_(jobs)))).all()
        per_job = []
        for job in rows:
            segments = await db.scalar(select(func.count()).select_from(TranscriptSegment)
                                       .where(TranscriptSegment.lecture_id == job.lecture_id))
            per_job.append({'job_id': str(job.id), 'status': job.status.value, 'attempts': job.attempts,
                            'transcript_rows': segments, 'killed': job.id in killed})
        await db.execute(delete(Lecture).where(Lecture.id.in_(ids)))
        await db.commit()
    await engine.dispose()

    transcription_calls = [line for line in calls.read_text().splitlines() if line]
    calls.unlink(missing_ok=True)
    report = {
        'scenario': 'real arq worker process hard-killed after persisted transcription checkpoint',
        'provider_calls': 'deterministic fixtures (no Deepgram/RAG)',
        'worker_lease_seconds': int(os.getenv('WORKER_LEASE_SECONDS', '90')),
        'jobs': args.jobs,
        'forced_kills': len(kill_log),
        'completed': sum(j['status'] == 'completed' for j in per_job),
        'failed': sum(j['status'] == 'failed' for j in per_job),
        'jobs_with_duplicate_rows': sum(j['transcript_rows'] != 1 for j in per_job),
        'transcription_calls': len(transcription_calls),
        'duplicate_transcription_calls': len(transcription_calls) - len(set(transcription_calls)),
        'wall_seconds': round(elapsed, 1),
        'kills': kill_log,
        'per_job': per_job,
    }
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(report, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k not in {'kills', 'per_job'}}, indent=2))
    ok = (report['forced_kills'] == args.kills and report['completed'] == args.jobs
          and report['jobs_with_duplicate_rows'] == 0 and report['duplicate_transcription_calls'] == 0)
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--jobs', type=int, default=10)
    parser.add_argument('--kills', type=int, default=10)
    parser.add_argument('--timeout', type=float, default=600)
    parser.add_argument('--output', default='artifacts/chaos-worker-kill.json')
    asyncio.run(main(parser.parse_args()))
