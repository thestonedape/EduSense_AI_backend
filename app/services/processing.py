import asyncio
import logging
import re
from datetime import datetime, timezone

from sqlalchemy import delete
from sqlalchemy import func
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.claim import Claim, ClaimVerdict
from app.models.knowledge import KnowledgeChunk
from app.models.lecture import Lecture, LectureStatus
from app.models.processing_job import ProcessingJob, ProcessingJobStatus, ProcessingJobType
from app.models.transcript import TopicSegment, TopicValidationState, TranscriptSegment
from app.services.deepgram import DeepgramTranscriptionService
from app.services.fact_check import FactCheckService
from app.services.knowledge import KnowledgeService
from app.services.lecture_accuracy import derive_accuracy_score
from app.services.reference_processing import ReferenceProcessingService
from app.services.semantic_pipeline import SemanticPipelineService
from app.services.storage import StorageService
from app.services.transcript import TranscriptService


settings = get_settings()
logger = logging.getLogger("app.processing")
TOKEN_PATTERN = re.compile(r"\w+")


class ProcessingService:
    def __init__(self) -> None:
        self.transcription_service = DeepgramTranscriptionService()
        self.semantic_pipeline = SemanticPipelineService()
        self.transcript_service = TranscriptService()
        self.knowledge_service = KnowledgeService()
        self.reference_processing_service = ReferenceProcessingService()
        self.fact_check_service = FactCheckService()
        self.storage_service = StorageService()

    async def create_job(
        self,
        session: AsyncSession,
        lecture_id,
        job_type: ProcessingJobType,
        *,
        stage: str = "queued",
        details: dict | None = None,
    ) -> ProcessingJob:
        previous_runs = await session.scalar(
            select(func.count())
            .select_from(ProcessingJob)
            .where(
                ProcessingJob.lecture_id == lecture_id,
                ProcessingJob.job_type == job_type,
            )
        )
        now = datetime.now(timezone.utc)
        job_details = {
            **(details or {}),
            "stage_history": [
                {
                    "stage": stage,
                    "status": ProcessingJobStatus.queued.value,
                    "at": now.isoformat(),
                }
            ],
        }
        job = ProcessingJob(
            lecture_id=lecture_id,
            job_type=job_type,
            status=ProcessingJobStatus.queued,
            stage=stage,
            retry_count=int(previous_runs or 0),
            details=job_details,
            last_heartbeat_at=now,
        )
        session.add(job)
        await session.flush()
        return job

    def _update_job(
        self,
        job: ProcessingJob | None,
        *,
        status: ProcessingJobStatus | None = None,
        stage: str | None = None,
        details: dict | None = None,
        error_message: str | None = None,
        finished: bool = False,
    ) -> None:
        if job is None:
            return

        now = datetime.now(timezone.utc)
        if status is not None:
            job.status = status
        if stage:
            job.stage = stage
        if error_message is not None:
            job.error_message = error_message
        job.last_heartbeat_at = now
        if job.status == ProcessingJobStatus.running and job.started_at is None:
            job.started_at = now
        if finished:
            job.finished_at = now

        job_details = job.details if isinstance(job.details, dict) else {}
        stage_history = list(job_details.get("stage_history", []))
        if stage:
            stage_history.append(
                {
                    "stage": stage,
                    "status": job.status.value,
                    "at": now.isoformat(),
                }
            )
            logger.info(
                "processing_job_transition lecture=%s job=%s type=%s status=%s stage=%s retry=%s",
                job.lecture_id,
                job.id,
                job.job_type.value,
                job.status.value,
                stage,
                job.retry_count,
            )
        job.details = {
            **job_details,
            **(details or {}),
            "stage_history": stage_history,
        }

    async def _commit_progress(
        self,
        session: AsyncSession,
        lecture: Lecture,
        *,
        progress: int | None = None,
        status: LectureStatus | None = None,
        error_message: str | None = None,
        job: ProcessingJob | None = None,
        job_status: ProcessingJobStatus | None = None,
        job_stage: str | None = None,
        job_details: dict | None = None,
        finished_job: bool = False,
    ) -> None:
        if progress is not None:
            lecture.progress = progress
        if status is not None:
            lecture.status = status
        if error_message is not None or error_message is None and status != LectureStatus.failed:
            lecture.error_message = error_message
        self._update_job(
            job,
            status=job_status,
            stage=job_stage,
            details=job_details,
            error_message=error_message if job_status == ProcessingJobStatus.failed else None,
            finished=finished_job,
        )
        await session.commit()

    async def run_pipeline(self, lecture_id, job_id=None) -> None:
        logger.info("pipeline_background_start lecture=%s job=%s", lecture_id, job_id)
        try:
            async with SessionLocal() as session:
                lecture = await session.scalar(
                    select(Lecture)
                    .where(Lecture.id == lecture_id)
                    .options(selectinload(Lecture.reference_files))
                )
                if lecture is None:
                    logger.warning("pipeline_background_missing_lecture lecture=%s job=%s", lecture_id, job_id)
                    return
                job = await session.scalar(select(ProcessingJob).where(ProcessingJob.id == job_id)) if job_id else None

                await self._commit_progress(
                    session,
                    lecture,
                    progress=10,
                    status=LectureStatus.processing,
                    error_message=None,
                    job=job,
                    job_status=ProcessingJobStatus.running,
                    job_stage="transcribing_lecture",
                    job_details={"lecture_id": str(lecture.id)},
                )
                print(f"[pipeline] lecture={lecture.id} stage=processing progress=10", flush=True)

                existing_metrics = lecture.metrics if isinstance(lecture.metrics, dict) else {}
                source_path = self.storage_service.ensure_local_path(lecture.storage_path, existing_metrics)
                transcription = self.transcription_service.transcribe(source_path)
                logger.info(
                    "pipeline_semantic_start lecture=%s openrouter_enabled=%s",
                    lecture.id,
                    self.semantic_pipeline.openrouter.is_configured,
                )
                cleaned_text, sentence_units, topic_units = self.semantic_pipeline.build_from_transcription(transcription)
                await session.execute(delete(TopicSegment).where(TopicSegment.lecture_id == lecture.id))
                await session.execute(delete(TranscriptSegment).where(TranscriptSegment.lecture_id == lecture.id))
                await session.commit()
                transcript_segments = self.transcript_service.build_segments(lecture.id, sentence_units)
                session.add_all(transcript_segments)
                lecture.summary = cleaned_text[:1000]
                await self._commit_progress(
                    session,
                    lecture,
                    progress=40,
                    job=job,
                    job_status=ProcessingJobStatus.running,
                    job_stage="transcript_segmented",
                    job_details={"transcript_segments": len(transcript_segments)},
                )
                print(
                    f"[pipeline] lecture={lecture.id} stage=transcription_complete segments={len(transcript_segments)} progress=40",
                    flush=True,
                )

                topics = self.transcript_service.build_topics(lecture.id, topic_units)
                session.add_all(topics)
                await self._commit_progress(
                    session,
                    lecture,
                    progress=60,
                    job=job,
                    job_status=ProcessingJobStatus.running,
                    job_stage="topics_grouped",
                    job_details={"topics": len(topics)},
                )
                print(
                    f"[pipeline] lecture={lecture.id} stage=topics_complete topics={len(topics)} progress=60",
                    flush=True,
                )

                await self._commit_progress(
                    session,
                    lecture,
                    job=job,
                    job_status=ProcessingJobStatus.running,
                    job_stage="knowledge_building",
                )
                await self.knowledge_service.rebuild_for_lecture(session, lecture.id, topics, transcript_segments)
                await self._commit_progress(
                    session,
                    lecture,
                    job=job,
                    job_status=ProcessingJobStatus.running,
                    job_stage="reference_processing",
                )
                reference_metrics = await self.reference_processing_service.process_reference_files(
                    session,
                    lecture.id,
                    list(lecture.reference_files),
                    transcript_segments,
                )
                await self._commit_progress(
                    session,
                    lecture,
                    progress=75,
                    job=job,
                    job_status=ProcessingJobStatus.running,
                    job_stage="reference_processing",
                    job_details=reference_metrics,
                )
                print(f"[pipeline] lecture={lecture.id} stage=knowledge_complete progress=75", flush=True)

                logger.info(
                    "pipeline_fact_check_start lecture=%s openrouter_enabled=%s candidate_source=%s",
                    lecture.id,
                    self.fact_check_service.openrouter.is_configured,
                    "openrouter" if self.fact_check_service.openrouter.is_configured else "heuristic",
                )
                await self._commit_progress(
                    session,
                    lecture,
                    job=job,
                    job_status=ProcessingJobStatus.running,
                    job_stage="fact_checking",
                )
                claims, fact_check_summary = await self.fact_check_service.generate_claims(session, lecture.id, transcript_segments)
                topic_validation_metrics = self._apply_topic_validation(topics, transcript_segments, claims)
                lecture.accuracy_score = self.calculate_accuracy(
                    fact_check_summary.candidate_count,
                    fact_check_summary.false_claim_count,
                )
                lecture.metrics = {
                    **existing_metrics,
                    "transcript_segments": len(transcript_segments),
                    "topics": len(topics),
                    "approved_topics": 0,
                    "claims": len(claims),
                    "fact_check_candidates": fact_check_summary.candidate_count,
                    "fact_check_false_claims": fact_check_summary.false_claim_count,
                    "fact_check_validation_source": fact_check_summary.validation_source,
                    "cleaned_sentences": len(sentence_units),
                    **topic_validation_metrics,
                    **reference_metrics,
                    "semantic_pipeline_version": "v2-openrouter" if self.semantic_pipeline.openrouter.is_configured else "v2",
                }
                await self._commit_progress(
                    session,
                    lecture,
                    progress=100,
                    status=LectureStatus.completed,
                    job=job,
                    job_status=ProcessingJobStatus.completed,
                    job_stage="completed",
                    job_details={
                        "claims": len(claims),
                        "fact_check_candidates": fact_check_summary.candidate_count,
                        "fact_check_false_claims": fact_check_summary.false_claim_count,
                    },
                    finished_job=True,
                )
                print(
                    f"[pipeline] lecture={lecture.id} stage=completed claims={len(claims)} progress=100",
                    flush=True,
                )
        except Exception as exc:
            logger.exception("pipeline_background_failed lecture=%s job=%s error=%s", lecture_id, job_id, exc)
            try:
                async with SessionLocal() as session:
                    lecture = await session.scalar(select(Lecture).where(Lecture.id == lecture_id))
                    job = await session.scalar(select(ProcessingJob).where(ProcessingJob.id == job_id)) if job_id else None
                    if lecture is not None:
                        await self._commit_progress(
                            session,
                            lecture,
                            progress=100,
                            status=LectureStatus.failed,
                            error_message=str(exc),
                            job=job,
                            job_status=ProcessingJobStatus.failed,
                            job_stage="failed",
                            job_details={"failed_stage": job.stage if job is not None else "pipeline"},
                            finished_job=True,
                        )
                        print(f"[pipeline] lecture={lecture.id} stage=failed error={exc}", flush=True)
            except Exception as persist_exc:
                logger.exception(
                    "pipeline_failure_state_persist_failed lecture=%s job=%s error=%s",
                    lecture_id,
                    job_id,
                    persist_exc,
                )

    def launch_pipeline(self, lecture_id, job_id=None) -> None:
        asyncio.run(self.run_pipeline(lecture_id, job_id))

    async def run_rebuild_structure(self, lecture_id, job_id=None) -> None:
        async with SessionLocal() as session:
            try:
                await self.rebuild_structure_from_existing_transcript(session, lecture_id, job_id=job_id)
            except Exception as exc:
                lecture = await session.scalar(select(Lecture).where(Lecture.id == lecture_id))
                job = await session.scalar(select(ProcessingJob).where(ProcessingJob.id == job_id)) if job_id else None
                if lecture is not None:
                    await self._commit_progress(
                        session,
                        lecture,
                        progress=100,
                        status=LectureStatus.failed,
                        error_message=str(exc),
                        job=job,
                        job_status=ProcessingJobStatus.failed,
                        job_stage="failed",
                        job_details={"failed_stage": job.stage if job is not None else "rebuild"},
                        finished_job=True,
                    )
                logger.exception("rebuild_background_failed lecture=%s error=%s", lecture_id, exc)

    def launch_rebuild_structure(self, lecture_id, job_id=None) -> None:
        asyncio.run(self.run_rebuild_structure(lecture_id, job_id))

    async def recover_orphaned_jobs(self) -> int:
        recovered = 0
        async with SessionLocal() as session:
            jobs = (
                await session.scalars(
                    select(ProcessingJob)
                    .where(ProcessingJob.status.in_([ProcessingJobStatus.queued, ProcessingJobStatus.running]))
                    .order_by(ProcessingJob.created_at.asc())
                )
            ).all()
            for job in jobs:
                lecture = await session.scalar(select(Lecture).where(Lecture.id == job.lecture_id))
                if lecture is None:
                    continue
                if job.job_type == ProcessingJobType.upload_pipeline and lecture.status == LectureStatus.completed:
                    continue
                if job.job_type == ProcessingJobType.rebuild_structure and lecture.status == LectureStatus.completed:
                    continue
                self._update_job(
                    job,
                    status=ProcessingJobStatus.queued,
                    stage=job.stage or "queued",
                    details={"recovered_on_startup": True},
                )
                if lecture.status == LectureStatus.failed:
                    lecture.status = LectureStatus.pending
                    lecture.error_message = None
                    lecture.progress = max(int(lecture.progress or 0), 5)
                await session.commit()
                if job.job_type == ProcessingJobType.upload_pipeline:
                    asyncio.create_task(self.run_pipeline(job.lecture_id, job.id))
                else:
                    asyncio.create_task(self.run_rebuild_structure(job.lecture_id, job.id))
                recovered += 1
                logger.info(
                    "processing_job_recovered lecture=%s job=%s type=%s",
                    job.lecture_id,
                    job.id,
                    job.job_type.value,
                )
        return recovered

    async def resume_latest_job(self, session: AsyncSession, lecture: Lecture) -> ProcessingJob:
        latest_job = await session.scalar(
            select(ProcessingJob)
            .where(ProcessingJob.lecture_id == lecture.id)
            .order_by(ProcessingJob.created_at.desc())
            .limit(1)
        )
        if latest_job is None:
            latest_job = await self.create_job(
                session,
                lecture.id,
                job_type=ProcessingJobType.upload_pipeline,
                details={"source": "manual_resume"},
            )
        latest_job.status = ProcessingJobStatus.queued
        latest_job.error_message = None
        latest_job.last_heartbeat_at = datetime.now(timezone.utc)
        lecture.status = LectureStatus.pending if latest_job.job_type == ProcessingJobType.upload_pipeline else LectureStatus.processing
        lecture.error_message = None
        lecture.progress = max(int(lecture.progress or 0), 5 if latest_job.job_type == ProcessingJobType.upload_pipeline else 55)
        await session.commit()
        return latest_job

    def _claim_overlap_score(self, left: str, right: str) -> float:
        left_tokens = set(TOKEN_PATTERN.findall(left.lower()))
        right_tokens = set(TOKEN_PATTERN.findall(right.lower()))
        if not left_tokens or not right_tokens:
            return 0.0
        return len(left_tokens & right_tokens) / len(left_tokens)

    def _resolve_topic_for_claim(
        self,
        claim_text: str,
        topics: list[TopicSegment],
        transcript_segments: list[TranscriptSegment],
    ) -> TopicSegment | None:
        if not topics:
            return None

        ordered_topics = sorted(topics, key=lambda item: item.sequence)
        ordered_segments = sorted(transcript_segments, key=lambda item: item.sequence)
        best_segment: TranscriptSegment | None = None
        best_segment_score = 0.0

        for segment in ordered_segments:
            segment_text = (segment.edited_text or segment.text).strip()
            score = self._claim_overlap_score(claim_text, segment_text)
            if score > best_segment_score:
                best_segment = segment
                best_segment_score = score

        if best_segment is not None and best_segment_score >= 0.2:
            for index, topic in enumerate(ordered_topics):
                next_topic = ordered_topics[index + 1] if index + 1 < len(ordered_topics) else None
                if best_segment.start_time < topic.start_time:
                    continue
                if next_topic is not None and best_segment.start_time >= next_topic.start_time:
                    continue
                return topic

        best_topic: TopicSegment | None = None
        best_topic_score = 0.0
        for topic in ordered_topics:
            score = max(
                self._claim_overlap_score(claim_text, topic.title),
                self._claim_overlap_score(claim_text, topic.summary),
            )
            if score > best_topic_score:
                best_topic = topic
                best_topic_score = score
        return best_topic if best_topic_score >= 0.15 else ordered_topics[0]

    def _apply_topic_validation(
        self,
        topics: list[TopicSegment],
        transcript_segments: list[TranscriptSegment],
        claims: list[Claim],
    ) -> dict[str, int]:
        counters_by_topic = {
            str(topic.id): {"claim_count": 0, "false_claim_count": 0}
            for topic in topics
        }

        for claim in claims:
            topic = self._resolve_topic_for_claim(claim.text, topics, transcript_segments)
            if topic is None:
                continue
            claim.topic_id = topic.id
            counters = counters_by_topic[str(topic.id)]
            counters["claim_count"] += 1
            if claim.verdict == ClaimVerdict.false:
                counters["false_claim_count"] += 1

        safe_topics = 0
        flagged_topics = 0
        unclear_topics = 0

        for topic in topics:
            counters = counters_by_topic[str(topic.id)]
            topic.claim_count = counters["claim_count"]
            topic.false_claim_count = counters["false_claim_count"]
            topic.approved_for_kb = False
            topic.reviewed_at = None
            topic.reviewed_by = None

            if topic.false_claim_count > 0:
                topic.validation_state = TopicValidationState.flagged
                topic.validation_reason = f"{topic.false_claim_count} flagged claim(s) linked to this topic."
                flagged_topics += 1
            elif not (topic.summary or "").strip():
                topic.validation_state = TopicValidationState.unclear
                topic.validation_reason = "Topic summary is missing, so manual review is required."
                unclear_topics += 1
            else:
                topic.validation_state = TopicValidationState.safe
                topic.validation_reason = (
                    "No false claims were linked to this topic during automated review."
                    if topic.claim_count
                    else "No flagged claims were generated for this topic."
                )
                safe_topics += 1

        return {
            "safe_topics": safe_topics,
            "flagged_topics": flagged_topics,
            "unclear_topics": unclear_topics,
        }

    def _mark_topics_unclear(self, topics: list[TopicSegment], reason: str) -> dict[str, int]:
        for topic in topics:
            topic.validation_state = TopicValidationState.unclear
            topic.validation_reason = reason
            topic.claim_count = 0
            topic.false_claim_count = 0
            topic.approved_for_kb = False
            topic.reviewed_at = None
            topic.reviewed_by = None
        return {
            "safe_topics": 0,
            "flagged_topics": 0,
            "unclear_topics": len(topics),
        }

    async def rebuild_structure_from_existing_transcript(self, session: AsyncSession, lecture_id, job_id=None) -> Lecture:
        stmt = (
            select(Lecture)
            .where(Lecture.id == lecture_id)
            .options(
                selectinload(Lecture.transcript_segments),
                selectinload(Lecture.topic_segments),
                selectinload(Lecture.reference_files),
            )
        )
        lecture = await session.scalar(stmt)
        if lecture is None:
            raise ValueError("Lecture not found.")
        job = await session.scalar(select(ProcessingJob).where(ProcessingJob.id == job_id)) if job_id else None

        transcript_segments = sorted(lecture.transcript_segments, key=lambda item: item.sequence)
        if not transcript_segments:
            raise ValueError("No stored transcript segments found for this lecture.")

        existing_metrics = lecture.metrics if isinstance(lecture.metrics, dict) else {}
        existing_topics = lecture.metrics.get("topics", 0) if isinstance(lecture.metrics, dict) else 0
        cleaned_text, rebuilt_sentence_units, rebuilt_topic_units = self.semantic_pipeline.build_from_sentences(
            [
                (segment.edited_text or segment.text, segment.start_time, segment.end_time)
                for segment in transcript_segments
            ]
        )
        logger.info(
            "rebuild_semantic_start lecture=%s openrouter_enabled=%s",
            lecture.id,
            self.semantic_pipeline.openrouter.is_configured,
        )
        rebuilt_segments = self.transcript_service.build_segments(lecture.id, rebuilt_sentence_units)

        await self._commit_progress(
            session,
            lecture,
            progress=55,
            status=LectureStatus.processing,
            error_message=None,
            job=job,
            job_status=ProcessingJobStatus.running,
            job_stage="rebuilding_topics",
            job_details={"lecture_id": str(lecture.id)},
        )
        await session.execute(delete(TopicSegment).where(TopicSegment.lecture_id == lecture.id))
        await session.execute(delete(TranscriptSegment).where(TranscriptSegment.lecture_id == lecture.id))
        await session.flush()

        session.add_all(rebuilt_segments)
        await session.flush()

        topics = self.transcript_service.build_topics(lecture.id, rebuilt_topic_units)
        session.add_all(topics)
        await session.flush()

        claims = []
        reference_metrics: dict = {}
        topic_validation_metrics = self._mark_topics_unclear(
            topics,
            "Topic validation is pending because the rebuild could not refresh downstream checks yet.",
        )
        downstream_refresh = "completed"
        try:
            await session.execute(delete(Claim).where(Claim.lecture_id == lecture.id))
            await session.execute(delete(KnowledgeChunk).where(KnowledgeChunk.lecture_id == lecture.id))
            await session.flush()
            await self._commit_progress(
                session,
                lecture,
                job=job,
                job_status=ProcessingJobStatus.running,
                job_stage="rebuilding_knowledge",
            )
            await self.knowledge_service.rebuild_for_lecture(session, lecture.id, topics, rebuilt_segments)
            await self._commit_progress(
                session,
                lecture,
                job=job,
                job_status=ProcessingJobStatus.running,
                job_stage="rebuilding_references",
            )
            reference_metrics = await self.reference_processing_service.process_reference_files(
                session,
                lecture.id,
                list(lecture.reference_files),
                rebuilt_segments,
            )
            logger.info(
                "rebuild_fact_check_start lecture=%s openrouter_enabled=%s candidate_source=%s",
                    lecture.id,
                    self.fact_check_service.openrouter.is_configured,
                    "openrouter" if self.fact_check_service.openrouter.is_configured else "heuristic",
                )
            await self._commit_progress(
                session,
                lecture,
                job=job,
                job_status=ProcessingJobStatus.running,
                job_stage="rebuilding_fact_check",
            )
            claims, fact_check_summary = await self.fact_check_service.generate_claims(session, lecture.id, rebuilt_segments)
            topic_validation_metrics = self._apply_topic_validation(topics, rebuilt_segments, claims)
        except Exception as exc:
            downstream_refresh = f"skipped: {exc}"
            await session.rollback()
            lecture = await session.scalar(stmt)
            if lecture is None:
                raise ValueError("Lecture not found after rebuild rollback.")
            transcript_segments = sorted(lecture.transcript_segments, key=lambda item: item.sequence)
            await session.execute(delete(TopicSegment).where(TopicSegment.lecture_id == lecture.id))
            await session.execute(delete(TranscriptSegment).where(TranscriptSegment.lecture_id == lecture.id))
            await session.flush()
            cleaned_text, rebuilt_sentence_units, rebuilt_topic_units = self.semantic_pipeline.build_from_sentences(
                [
                    (segment.edited_text or segment.text, segment.start_time, segment.end_time)
                    for segment in transcript_segments
                ]
            )
            rebuilt_segments = self.transcript_service.build_segments(lecture.id, rebuilt_sentence_units)
            session.add_all(rebuilt_segments)
            await session.flush()
            topics = self.transcript_service.build_topics(lecture.id, rebuilt_topic_units)
            session.add_all(topics)
            await session.flush()
            fact_check_summary = None
            topic_validation_metrics = self._mark_topics_unclear(
                topics,
                "Topic validation is pending because the rebuild could not refresh downstream checks yet.",
            )

        lecture.summary = cleaned_text[:1000]
        lecture.accuracy_score = derive_accuracy_score(
            stored_accuracy=lecture.accuracy_score,
            metrics={
                **existing_metrics,
                "fact_check_candidates": fact_check_summary.candidate_count if fact_check_summary else None,
                "fact_check_false_claims": len(claims),
                "semantic_pipeline_version": "v2-openrouter" if self.semantic_pipeline.openrouter.is_configured else "v2",
            },
            claim_count=len(claims),
            status=LectureStatus.completed,
        )
        lecture.metrics = {
            **existing_metrics,
            "transcript_segments": len(rebuilt_segments),
            "topics": len(topics),
            "approved_topics": 0,
            "claims": len(claims),
            "fact_check_candidates": fact_check_summary.candidate_count if fact_check_summary else 0,
            "fact_check_false_claims": len(claims),
            "fact_check_validation_source": fact_check_summary.validation_source if fact_check_summary else "unavailable",
            "structure_rebuilt_from_existing_transcript": True,
            "previous_topics": existing_topics,
            "downstream_refresh": downstream_refresh,
            "cleaned_sentences": len(rebuilt_sentence_units),
            **topic_validation_metrics,
            **reference_metrics,
            "semantic_pipeline_version": "v2-openrouter" if self.semantic_pipeline.openrouter.is_configured else "v2",
        }
        await self._commit_progress(
            session,
            lecture,
            progress=100,
            status=LectureStatus.completed,
            job=job,
            job_status=ProcessingJobStatus.completed,
            job_stage="completed",
            job_details={
                "claims": len(claims),
                "downstream_refresh": downstream_refresh,
            },
            finished_job=True,
        )
        await session.refresh(lecture)
        return lecture

    def calculate_accuracy(self, candidate_count: int, false_claim_count: int) -> float:
        if candidate_count <= 0:
            return 100.0
        bounded_false_count = min(max(false_claim_count, 0), candidate_count)
        return round(((candidate_count - bounded_false_count) / candidate_count) * 100, 2)
