"""Real PostgreSQL/Redis ledger tests; only external processing is replaced."""
import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
import pytest

pytestmark=pytest.mark.skipif(not os.getenv('SDE_TEST_DATABASE_URL'),reason='Explicit isolated test database required')

def test_interruption_checkpoint_redis_loss_and_fencing(monkeypatch):
    async def scenario():
        from sqlalchemy import delete, func, select
        from arq.connections import create_pool
        from app.db.session import SessionLocal, engine
        from app.models.lecture import Lecture, LectureStatus
        from app.models.processing_job import ProcessingJob, ProcessingJobStatus, ProcessingJobType
        from app.models.job_outbox import JobOutbox
        from app.models.transcript import TranscriptSegment
        from app.services.processing import ProcessingService
        from app.workers.queue import execute_job, reconcile, WorkerSettings
        assert engine.url.database.endswith('_test')
        assert engine.url.render_as_string(hide_password=False)==os.environ['SDE_TEST_DATABASE_URL']
        pool=await create_pool(WorkerSettings.redis_settings)
        ids=[]
        paused=asyncio.Event()
        async def pipeline(self,lecture_id,job_id):
            async with SessionLocal() as db:
                lecture=await db.get(Lecture,lecture_id);job=await db.get(ProcessingJob,job_id)
                if job.attempts==1:
                    await self._commit_progress(db,lecture,job=job,job_details={'transcription_checkpoint':{'text':'fixture'}})
                    paused.set();await asyncio.Event().wait()
                assert job.details['transcription_checkpoint']['text']=='fixture'
                await db.execute(delete(TranscriptSegment).where(TranscriptSegment.lecture_id==lecture_id))
                db.add(TranscriptSegment(lecture_id=lecture_id,sequence=1,start_time=0,end_time=1,text='fixture'))
                await self._commit_progress(db,lecture,status=LectureStatus.completed,job=job,job_status=ProcessingJobStatus.completed,finished_job=True)
        monkeypatch.setattr(ProcessingService,'run_pipeline',pipeline)
        class UnavailableRedis:
            async def enqueue_job(self,*args,**kwargs):raise ConnectionError('Forced enqueue outage')
        try:
            for _ in range(10):
                paused.clear()
                async with SessionLocal() as db:
                    lecture=Lecture(lecture_name='fixture',original_filename='fixture.wav',storage_path='unused-fixture',course='test',module='test',metrics={})
                    db.add(lecture);await db.flush();ids.append(lecture.id)
                    job=await ProcessingService().create_job(db,lecture.id,ProcessingJobType.upload_pipeline)
                    await db.commit();job_id=job.id
                await reconcile({'redis':UnavailableRedis()})
                async with SessionLocal() as db:
                    stored=await db.get(ProcessingJob,job_id)
                    assert stored.status==ProcessingJobStatus.queued
                    assert await db.get(JobOutbox,job_id)
                task=asyncio.create_task(execute_job({},str(job_id)))
                await asyncio.wait_for(paused.wait(),10)
                task.cancel();await asyncio.gather(task,return_exceptions=True)
                async with SessionLocal() as db:
                    stored=await db.get(ProcessingJob,job_id)
                    old_token=stored.lease_token
                    stored.lease_expires_at=datetime.now(timezone.utc)-timedelta(seconds=1)
                    outbox=await db.get(JobOutbox,job_id);outbox.next_attempt_at=datetime.now(timezone.utc)-timedelta(seconds=1)
                    await db.commit()
                await reconcile({'redis':pool})
                await execute_job({},str(job_id))
                await execute_job({},str(job_id))  # duplicate at-least-once delivery
                async with SessionLocal() as db:
                    stored=await db.get(ProcessingJob,job_id)
                    assert stored.status==ProcessingJobStatus.completed and stored.attempts==2
                    assert await db.scalar(select(func.count()).select_from(TranscriptSegment).where(TranscriptSegment.lecture_id==lecture.id))==1
                    service=ProcessingService();service.lease_token=old_token
                    with pytest.raises(RuntimeError,match='Worker lease lost'):
                        await service._commit_progress(db,await db.get(Lecture,lecture.id),job=stored,job_status=ProcessingJobStatus.failed)
            await pool.ping()
        finally:
            async with SessionLocal() as db:
                await db.execute(delete(Lecture).where(Lecture.id.in_(ids)));await db.commit()
            await pool.aclose();await engine.dispose()
    asyncio.run(scenario())
