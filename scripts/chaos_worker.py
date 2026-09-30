"""arq worker whose provider stages are deterministic fixtures, for process-kill tests.

Only transcription/RAG calls are replaced; leases, heartbeats, the outbox, checkpoints
and stage writes are the production code paths. Never point this at a live database.
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if not os.environ['DATABASE_URL'].rsplit('/', 1)[-1].endswith('_test'):
    raise SystemExit('chaos worker requires a *_test database')

from arq import run_worker
from sqlalchemy import delete
from app.db.session import SessionLocal
from app.models.lecture import Lecture, LectureStatus
from app.models.processing_job import ProcessingJob, ProcessingJobStatus
from app.models.transcript import TranscriptSegment
from app.services.processing import ProcessingService
from app.workers.queue import WorkerSettings

CALLS = Path(os.environ['CHAOS_CALL_LOG'])
STAGE_SECONDS = float(os.getenv('CHAOS_STAGE_SECONDS', '4'))


async def pipeline(self, lecture_id, job_id):
    async with SessionLocal() as db:
        lecture = await db.get(Lecture, lecture_id)
        job = await db.get(ProcessingJob, job_id)
        checkpoint = (job.details or {}).get('transcription_checkpoint')
        if not checkpoint:
            # Stand-in for the paid transcription call; logged so reuse can be audited.
            with CALLS.open('a') as log:
                log.write(f'{lecture_id}\n')
            await self._commit_progress(db, lecture, job=job, job_details={'transcription_checkpoint': {'text': 'fixture'}})
        await asyncio.sleep(STAGE_SECONDS)  # validation/indexing window where kills land
        await db.execute(delete(TranscriptSegment).where(TranscriptSegment.lecture_id == lecture_id))
        db.add(TranscriptSegment(lecture_id=lecture_id, sequence=1, start_time=0, end_time=1, text='fixture'))
        await self._commit_progress(db, lecture, status=LectureStatus.completed, job=job,
                                    job_status=ProcessingJobStatus.completed, finished_job=True)


ProcessingService.run_pipeline = pipeline

if __name__ == '__main__':
    run_worker(WorkerSettings)
