import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from arq import cron
from arq.connections import RedisSettings
from sqlalchemy import select, or_, and_
from app.db.session import SessionLocal
from app.models.processing_job import ProcessingJob, ProcessingJobStatus, ProcessingJobType
from app.models.job_outbox import JobOutbox
from app.models.lecture import Lecture, LectureStatus
from app.services.processing import ProcessingService

LEASE_SECONDS = int(os.getenv('WORKER_LEASE_SECONDS', '90'))
HEARTBEAT_SECONDS = max(1, LEASE_SECONDS // 6)
RECONCILE_SECONDS = {0, 15, 30, 45} if LEASE_SECONDS >= 60 else set(range(0, 60, 3))

_dispatch_pool = None

async def dispatch_now(job_id):
    """Best-effort fast path after commit; the outbox reconcile loop remains the guarantee."""
    global _dispatch_pool
    try:
        if _dispatch_pool is None:
            from arq.connections import create_pool
            _dispatch_pool = await asyncio.wait_for(create_pool(WorkerSettings.redis_settings), 2)
        await asyncio.wait_for(_dispatch_pool.enqueue_job('execute_job', str(job_id), _job_id=f'lecture:{job_id}:0', _queue_name='edusense:queue'), 2)
    except Exception:
        _dispatch_pool = None

async def worker_presence(ctx):
    while True:
        try:
            await ctx['redis'].set('edusense:worker:ready', '1', ex=45)
        except Exception:
            pass
        await asyncio.sleep(15)

async def startup(ctx):
    ctx['presence'] = asyncio.create_task(worker_presence(ctx))

async def shutdown(ctx):
    ctx['presence'].cancel()
    await asyncio.gather(ctx['presence'], return_exceptions=True)
    await ctx['redis'].delete('edusense:worker:ready')

async def reconcile(ctx):
    """Enqueue committed jobs, including Redis loss and expired worker leases."""
    now = datetime.now(timezone.utc)
    async with SessionLocal() as session:
        jobs = (await session.scalars(select(ProcessingJob).where(or_(
            ProcessingJob.status == ProcessingJobStatus.queued,
            and_(ProcessingJob.status == ProcessingJobStatus.running, or_(ProcessingJob.lease_expires_at < now, ProcessingJob.lease_expires_at.is_(None)))
        )).limit(50).with_for_update(skip_locked=True))).all()
        for job in jobs:
            outbox = await session.get(JobOutbox, job.id)
            if outbox is None:
                outbox = JobOutbox(job_id=job.id, next_attempt_at=now)
                session.add(outbox)
                await session.flush()
            if outbox.next_attempt_at and outbox.next_attempt_at > now:
                continue
            if job.status == ProcessingJobStatus.running:
                job.status = ProcessingJobStatus.queued
                job.lease_token = None
                job.lease_expires_at = None
            if job.attempts >= 3:
                job.status = ProcessingJobStatus.failed
                job.error_message = 'Retry limit exhausted'
                job.finished_at = now
                lecture = await session.get(Lecture, job.lecture_id)
                if lecture:
                    lecture.status = LectureStatus.failed
                    lecture.error_message = 'Retry limit exhausted'
                continue
            try:
                await ctx['redis'].enqueue_job('execute_job', str(job.id), _job_id=f'lecture:{job.id}:{job.attempts}', _queue_name='edusense:queue')
                outbox.delivered_at = now
                # Re-dispatch until claimed: Redis is not the durable source of truth.
                outbox.next_attempt_at = now + timedelta(seconds=30)
            except Exception:
                outbox.next_attempt_at = now + timedelta(seconds=30)
            outbox.dispatch_attempts += 1
        await session.commit()

async def heartbeat(job_id, token):
    while True:
        await asyncio.sleep(HEARTBEAT_SECONDS)
        async with SessionLocal() as session:
            job = await session.scalar(select(ProcessingJob).where(ProcessingJob.id == job_id).with_for_update())
            if not job or job.lease_token != token or job.status != ProcessingJobStatus.running:
                return
            now = datetime.now(timezone.utc)
            job.last_heartbeat_at = now
            job.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
            await session.commit()

async def execute_job(ctx, job_id):
    job_id, token = uuid.UUID(job_id), uuid.uuid4()
    now = datetime.now(timezone.utc)
    async with SessionLocal() as session:
        job = await session.scalar(select(ProcessingJob).where(ProcessingJob.id == job_id).with_for_update())
        if not job or job.status in {ProcessingJobStatus.completed, ProcessingJobStatus.failed} or job.attempts >= 3:
            return
        if job.lease_expires_at and job.lease_expires_at > now:
            return
        job.attempts += 1
        job.status = ProcessingJobStatus.running
        job.lease_token = token
        job.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
        lecture_id, job_type = job.lecture_id, job.job_type
        await session.commit()
    ticker = asyncio.create_task(heartbeat(job_id, token))
    service = ProcessingService()
    service.lease_token = token
    try:
        action = service.run_pipeline if job_type == ProcessingJobType.upload_pipeline else service.run_rebuild_structure
        await asyncio.wait_for(action(lecture_id, job_id), timeout=1800)
    except Exception as exc:
        import requests
        retryable = isinstance(exc, (requests.ConnectionError, requests.Timeout, TimeoutError)) or (isinstance(exc, requests.HTTPError) and exc.response is not None and (exc.response.status_code == 429 or exc.response.status_code >= 500))
        async with SessionLocal() as session:
            job = await session.scalar(select(ProcessingJob).where(ProcessingJob.id == job_id).with_for_update())
            if job and job.lease_token == token:
                job.status = ProcessingJobStatus.queued if retryable and job.attempts < 3 else ProcessingJobStatus.failed
                job.error_message = type(exc).__name__
                job.lease_expires_at = None
                job.lease_token = None
                lecture = await session.get(Lecture, job.lecture_id)
                if lecture:
                    lecture.status = LectureStatus.pending if job.status == ProcessingJobStatus.queued else LectureStatus.failed
                    lecture.error_message = None if job.status == ProcessingJobStatus.queued else type(exc).__name__
                if job.status == ProcessingJobStatus.failed:
                    job.finished_at = datetime.now(timezone.utc)
                outbox = await session.get(JobOutbox, job_id)
                if outbox:
                    outbox.next_attempt_at = datetime.now(timezone.utc) + timedelta(seconds=5 * 4 ** max(0, job.attempts - 1))
                await session.commit()
    finally:
        ticker.cancel()
        await asyncio.gather(ticker, return_exceptions=True)
        # Only disposable Supabase download copies are removed. Originals stay durable.
        try:
            from app.models.reference_file import ReferenceFile
            from pathlib import Path
            async with SessionLocal() as session:
                lecture = await session.get(Lecture, lecture_id)
                references = (await session.scalars(select(ReferenceFile).where(ReferenceFile.lecture_id == lecture_id))).all()
                metadata = [lecture.metrics] if lecture else []
                metadata += [reference.details for reference in references]
                for details in metadata:
                    if isinstance(details, dict) and details.get('storage_backend') == 'supabase':
                        local = service.storage_service.upload_dir / Path(details['supabase_object_path']).name
                        local.unlink(missing_ok=True)
        except Exception:
            pass  # Worker restart/download is independent of local scratch copies.

class WorkerSettings:
    queue_name = 'edusense:queue'
    on_startup = startup
    on_shutdown = shutdown
    functions = [execute_job]
    cron_jobs = [cron(reconcile, second=RECONCILE_SECONDS, run_at_startup=True)]
    redis_settings = RedisSettings.from_dsn(os.getenv('REDIS_URL', 'redis://localhost:6379/0'))
    max_jobs = 1
    job_timeout = 1900
    keep_result = 0
    poll_delay = 0.5
