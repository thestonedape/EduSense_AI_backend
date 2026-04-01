from __future__ import annotations

import logging
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from time import perf_counter

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.claim import Claim, ClaimVerdict
from app.models.knowledge import KnowledgeChunk
from app.models.lecture import Lecture, LectureStatus
from app.models.reference_file import ReferenceFile
from app.models.transcript import TopicSegment
from app.schemas.student import (
    StudentChatCitation,
    StudentChatResponse,
    StudentDashboardResponse,
    StudentDashboardStats,
    StudentDoubtResponse,
    StudentLectureDetail,
    StudentLectureSummary,
    StudentPracticeQuestion,
    StructuredStudyAnswer,
    StudentSubjectDetail,
    StudentSubjectSummary,
    StudentTopic,
)
from app.services.knowledge import KnowledgeService
from app.services.openrouter import OpenRouterService
from app.services.student_persistence import StudentPersistenceService

logger = logging.getLogger("app.student_portal")


@dataclass
class ValidatedLectureBundle:
    lecture: Lecture
    false_claim_count: int
    reference_count: int
    topic_count: int


class StudentPortalService:
    def __init__(self) -> None:
        self.knowledge_service = KnowledgeService()
        self.openrouter = OpenRouterService()
        self.persistence = StudentPersistenceService()

    def _subject_id(self, lecture: Lecture) -> str:
        if lecture.subject_key:
            return lecture.subject_key
        raw = " ".join(
            value
            for value in [
                lecture.department_name or "",
                lecture.program_name or "",
                lecture.subject_code or "",
                lecture.subject_name or lecture.lecture_name,
            ]
            if value
        ).strip()
        slug = re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")
        return slug or str(lecture.id)

    def _subject_description(self, lecture: Lecture, lecture_count: int, reference_count: int) -> str:
        subject_name = lecture.subject_name or lecture.subject_code or "subject"
        if reference_count:
            return (
                f"{subject_name} includes {lecture_count} validated lecture"
                f"{'' if lecture_count == 1 else 's'} and {reference_count} trusted reference file"
                f"{'' if reference_count == 1 else 's'} for guided study."
            )
        return (
            f"{subject_name} includes {lecture_count} validated lecture"
            f"{'' if lecture_count == 1 else 's'} backed by the reviewed lecture knowledge base."
        )

    async def _validated_lecture_bundles(
        self,
        session: AsyncSession,
        *,
        lecture_ids: list[uuid.UUID] | None = None,
        include_reference_files: bool = True,
        include_topic_segments: bool = True,
        include_knowledge_chunks: bool = True,
    ) -> list[ValidatedLectureBundle]:
        false_claims = (
            select(Claim.lecture_id, func.count(Claim.id).label("false_claim_count"))
            .where(Claim.verdict == ClaimVerdict.false)
            .group_by(Claim.lecture_id)
            .subquery()
        )
        reference_counts = (
            select(ReferenceFile.lecture_id, func.count(ReferenceFile.id).label("reference_count"))
            .group_by(ReferenceFile.lecture_id)
            .subquery()
        )
        topic_counts = (
            select(TopicSegment.lecture_id, func.count(TopicSegment.id).label("topic_count"))
            .group_by(TopicSegment.lecture_id)
            .subquery()
        )

        stmt: Select[tuple[Lecture, int, int, int]] = (
            select(
                Lecture,
                func.coalesce(false_claims.c.false_claim_count, 0),
                func.coalesce(reference_counts.c.reference_count, 0),
                func.coalesce(topic_counts.c.topic_count, 0),
            )
            .outerjoin(false_claims, false_claims.c.lecture_id == Lecture.id)
            .outerjoin(reference_counts, reference_counts.c.lecture_id == Lecture.id)
            .outerjoin(topic_counts, topic_counts.c.lecture_id == Lecture.id)
            .where(Lecture.status == LectureStatus.completed)
            .where(func.coalesce(false_claims.c.false_claim_count, 0) == 0)
            .order_by(Lecture.lecture_date.desc().nullslast(), Lecture.created_at.desc())
        )
        if lecture_ids:
            stmt = stmt.where(Lecture.id.in_(lecture_ids))

        load_options = []
        if include_reference_files:
            load_options.append(selectinload(Lecture.reference_files))
        if include_topic_segments:
            load_options.append(selectinload(Lecture.topic_segments))
        if include_knowledge_chunks:
            load_options.append(selectinload(Lecture.knowledge_chunks))
        if load_options:
            stmt = stmt.options(*load_options)

        rows = await session.execute(stmt)
        return [
            ValidatedLectureBundle(
                lecture=lecture,
                false_claim_count=int(false_claim_count or 0),
                reference_count=int(reference_count or 0),
                topic_count=int(topic_count or 0),
            )
            for lecture, false_claim_count, reference_count, topic_count in rows.all()
        ]

    def _knowledge_topics(self, lecture: Lecture) -> list[StudentTopic]:
        topics: list[StudentTopic] = []
        seen: set[tuple[str, str]] = set()

        for chunk in lecture.knowledge_chunks:
            kind = str(chunk.details.get("kind", ""))
            if not bool(chunk.details.get("student_visible")):
                continue
            if kind not in {"reference_topic", "topic"}:
                continue
            source = "Reference" if kind.startswith("reference_") else "Lecture"
            key = (chunk.topic, source)
            if key in seen:
                continue
            seen.add(key)
            topics.append(
                StudentTopic(
                    id=str(chunk.id),
                    title=chunk.topic,
                    summary=chunk.content[:500].strip(),
                    source=source,
                )
            )

        return topics

    def _lecture_summary(self, lecture: Lecture, *, reference_count: int | None = None) -> str:
        if lecture.summary and lecture.summary.strip():
            return lecture.summary.strip()[:800]
        visible_summaries = [
            chunk.content.strip()
            for chunk in lecture.knowledge_chunks
            if bool(chunk.details.get("student_visible")) and str(chunk.details.get("kind", "")) in {"reference_topic", "topic", "reference_full"}
        ]
        if visible_summaries:
            return " ".join(visible_summaries[:2])[:800].strip()
        if (reference_count or 0) > 0 or lecture.reference_files:
            return "Trusted reference content is attached, but student-facing lecture topics are still waiting for approval."
        return "This lecture is waiting for topic approval before lecture notes are shown to students."

    def _recommended_questions(self, lecture: Lecture) -> list[str]:
        base = [
            "What is the main idea introduced in this lecture?",
            "Which topic should I revise first from this lecture?",
        ]
        topic_titles = [
            topic.title
            for topic in self._knowledge_topics(lecture)[:3]
        ]
        for title in topic_titles:
            base.append(f"Can you explain {title} in simple terms?")
        return base[:4]

    def _validation_source(self, lecture: Lecture, *, reference_count: int | None = None) -> str:
        if (reference_count or 0) > 0:
            return "reference-backed"
        source = lecture.metrics.get("fact_check_validation_source")
        if isinstance(source, str) and source:
            return source.replace("_", "-")
        return "model-reviewed"

    def _is_small_talk(self, message: str) -> bool:
        normalized = re.sub(r"[^a-z0-9\s]", " ", message.lower()).strip()
        if not normalized:
            return True
        small_talk_phrases = {
            "hi",
            "hello",
            "hey",
            "yo",
            "thanks",
            "thank you",
            "ok",
            "okay",
            "cool",
            "good morning",
            "good evening",
            "good afternoon",
        }
        return normalized in small_talk_phrases

    def _small_talk_response(self, subject_scope: str | None = None) -> str:
        if subject_scope:
            return (
                f"Hi! Ask me any doubt from {subject_scope}, and I will explain it clearly. "
                "You can also ask for a simpler version, examples, or exam-focused notes."
            )
        return (
            "Hi! Ask me any doubt from your approved study material, and I will explain it clearly. "
            "You can also ask for a simpler version, examples, or exam-focused notes."
        )

    def _should_emit_structured_answer(self, message: str) -> bool:
        normalized = re.sub(r"[^a-z0-9\s]", " ", message.lower()).strip()
        structured_signals = {
            "key takeaways",
            "takeaways",
            "notes",
            "short note",
            "long answer",
            "exam answer",
            "exam point",
            "bullet points",
            "summarize",
            "summary",
            "in points",
            "study note",
        }
        return any(signal in normalized for signal in structured_signals)

    def _fallback_structured_answer(self, response: str) -> StructuredStudyAnswer | None:
        cleaned = response.strip()
        if not cleaned:
            return None
        return StructuredStudyAnswer(
            core_concept=cleaned,
            key_takeaways=[],
        )

    async def get_dashboard(self, session: AsyncSession, *, student_email: str) -> StudentDashboardResponse:
        started_at = perf_counter()
        stats = await self.persistence.get_dashboard_stats(session, student_email=student_email)
        stats_duration = (perf_counter() - started_at) * 1000
        recent_progress = await self.persistence.get_recent_progress(session, student_email=student_email)
        progress_duration = (perf_counter() - started_at) * 1000
        recent_ids = [progress.lecture_id for progress in recent_progress]
        lecture_map = {
            bundle.lecture.id: bundle
            for bundle in await self._validated_lecture_bundles(
                session,
                lecture_ids=recent_ids or None,
                include_reference_files=False,
                include_topic_segments=False,
                include_knowledge_chunks=False,
            )
        }
        bundle_duration = (perf_counter() - started_at) * 1000
        recent_lectures: list[StudentLectureSummary] = []

        for progress in recent_progress:
            bundle = lecture_map.get(progress.lecture_id)
            if bundle is None:
                continue
            recent_lectures.append(
                StudentLectureSummary(
                    id=bundle.lecture.id,
                    subject_id=self._subject_id(bundle.lecture),
                    lecture_name=bundle.lecture.lecture_name,
                    lecture_number=bundle.lecture.lecture_number,
                    lecture_date=bundle.lecture.lecture_date.isoformat() if bundle.lecture.lecture_date else None,
                    faculty_name=bundle.lecture.faculty_name,
                    summary=self._lecture_summary(bundle.lecture, reference_count=bundle.reference_count),
                    topic_count=bundle.topic_count,
                    reference_count=bundle.reference_count,
                    validation_source=self._validation_source(bundle.lecture, reference_count=bundle.reference_count),
                    progress_status=progress.status.value,
                    last_opened_at=progress.last_opened_at.isoformat() if progress.last_opened_at else None,
                )
            )

        response = StudentDashboardResponse(
            stats=StudentDashboardStats(**stats),
            recent_lectures=recent_lectures,
        )
        total_duration = (perf_counter() - started_at) * 1000
        if total_duration >= 250:
            logger.info(
                "student_dashboard_timing student=%s stats_ms=%.1f progress_ms=%.1f bundles_ms=%.1f total_ms=%.1f recent_count=%s",
                student_email,
                stats_duration,
                max(progress_duration - stats_duration, 0),
                max(bundle_duration - progress_duration, 0),
                total_duration,
                len(recent_lectures),
            )
        return response

    async def list_subjects(self, session: AsyncSession) -> list[StudentSubjectSummary]:
        bundles = await self._validated_lecture_bundles(
            session,
            include_reference_files=False,
            include_topic_segments=False,
            include_knowledge_chunks=False,
        )
        grouped: dict[str, list[ValidatedLectureBundle]] = defaultdict(list)
        for bundle in bundles:
            grouped[self._subject_id(bundle.lecture)].append(bundle)

        subjects: list[StudentSubjectSummary] = []
        for subject_id, subject_bundles in grouped.items():
            lecture = subject_bundles[0].lecture
            lecture_count = len(subject_bundles)
            reference_count = sum(bundle.reference_count for bundle in subject_bundles)
            latest_date = max(
                (bundle.lecture.lecture_date for bundle in subject_bundles if bundle.lecture.lecture_date is not None),
                default=None,
            )
            subjects.append(
                StudentSubjectSummary(
                    id=subject_id,
                    name=lecture.subject_name or lecture.lecture_name,
                    code=lecture.subject_code or "SUBJECT",
                    department_name=lecture.department_name,
                    program_name=lecture.program_name,
                    lecture_count=lecture_count,
                    reference_count=reference_count,
                    description=self._subject_description(lecture, lecture_count, reference_count),
                    latest_lecture_date=latest_date.isoformat() if isinstance(latest_date, date) else None,
                )
            )

        subjects.sort(key=lambda item: (item.name.lower(), item.code.lower()))
        return subjects

    async def get_subject(self, session: AsyncSession, subject_id: str) -> StudentSubjectDetail | None:
        bundles = await self._validated_lecture_bundles(session)
        subject_bundles = [bundle for bundle in bundles if self._subject_id(bundle.lecture) == subject_id]
        if not subject_bundles:
            return None

        lecture = subject_bundles[0].lecture
        subject = StudentSubjectSummary(
            id=subject_id,
            name=lecture.subject_name or lecture.lecture_name,
            code=lecture.subject_code or "SUBJECT",
            department_name=lecture.department_name,
            program_name=lecture.program_name,
            lecture_count=len(subject_bundles),
            reference_count=sum(bundle.reference_count for bundle in subject_bundles),
            description=self._subject_description(
                lecture,
                len(subject_bundles),
                sum(bundle.reference_count for bundle in subject_bundles),
            ),
            latest_lecture_date=max(
                (
                    bundle.lecture.lecture_date.isoformat()
                    for bundle in subject_bundles
                    if bundle.lecture.lecture_date is not None
                ),
                default=None,
            ),
        )

        lectures = [
            StudentLectureSummary(
                id=bundle.lecture.id,
                subject_id=subject_id,
                lecture_name=bundle.lecture.lecture_name,
                lecture_number=bundle.lecture.lecture_number,
                lecture_date=bundle.lecture.lecture_date.isoformat() if bundle.lecture.lecture_date else None,
                faculty_name=bundle.lecture.faculty_name,
                summary=self._lecture_summary(bundle.lecture),
                topic_count=bundle.topic_count or len(bundle.lecture.topic_segments),
                reference_count=bundle.reference_count,
                    validation_source=self._validation_source(bundle.lecture, reference_count=bundle.reference_count),
                progress_status=None,
                last_opened_at=None,
            )
            for bundle in subject_bundles
        ]
        return StudentSubjectDetail(subject=subject, lectures=lectures)

    async def get_lecture(self, session: AsyncSession, lecture_id: uuid.UUID) -> StudentLectureDetail | None:
        bundles = await self._validated_lecture_bundles(session)
        bundle = next((item for item in bundles if item.lecture.id == lecture_id), None)
        if bundle is None:
            return None

        lecture = bundle.lecture
        subject_id = self._subject_id(lecture)
        return StudentLectureDetail(
            id=lecture.id,
            subject_id=subject_id,
            lecture_name=lecture.lecture_name,
            subject_name=lecture.subject_name,
            subject_code=lecture.subject_code,
            department_name=lecture.department_name,
            program_name=lecture.program_name,
            lecture_number=lecture.lecture_number,
            lecture_date=lecture.lecture_date.isoformat() if lecture.lecture_date else None,
            faculty_name=lecture.faculty_name,
            summary=self._lecture_summary(lecture),
            reference_files=[item.original_filename for item in lecture.reference_files],
            topics=self._knowledge_topics(lecture),
            recommended_questions=self._recommended_questions(lecture),
            validation_source=self._validation_source(lecture, reference_count=bundle.reference_count),
        )

    async def answer_question(
        self,
        session: AsyncSession,
        *,
        lecture_id: uuid.UUID,
        message: str,
        student_email: str,
    ) -> StudentChatResponse | None:
        lecture = await self.get_lecture(session, lecture_id)
        if lecture is None:
            return None

        lecture_chunks = await self.knowledge_service.search(
            session,
            query=message,
            limit=3,
            lecture_id=lecture_id,
            approved_only=True,
        )
        if not lecture_chunks:
            response = "I could not find enough validated lecture context for that question yet."
            return StudentChatResponse(response=response, citations=[])

        citations = [
            StudentChatCitation(
                topic=chunk.topic,
                source="Reference" if str(chunk.details.get("kind", "")).startswith("reference_") else "Lecture",
                excerpt=chunk.content[:280].strip(),
            )
            for chunk in lecture_chunks
            if isinstance(chunk, KnowledgeChunk)
        ]

        answer = None
        if self.openrouter.is_configured and lecture_chunks:
            try:
                answer = self.openrouter.answer_student_question(
                    question=message,
                    lecture_title=lecture.lecture_name,
                    subject_context=lecture.subject_name or lecture.subject_code,
                    context_items=[
                        {
                            "topic": chunk.topic,
                            "source": "Reference" if str(chunk.details.get("kind", "")).startswith("reference_") else "Lecture",
                            "content": chunk.content,
                        }
                        for chunk in lecture_chunks
                        if isinstance(chunk, KnowledgeChunk)
                    ],
                )
            except Exception:
                answer = None

        if answer is None:
            if citations:
                response = (
                    f"Here’s the clearest study answer from the validated lecture material: {citations[0].excerpt} "
                    "Use the cited topic cards below if you want a more detailed explanation."
                )
            else:
                response = "I could not find enough validated lecture context for that question yet."
        else:
            response = answer

        await self.persistence.append_chat_exchange(
            session,
            student_email=student_email,
            lecture_id=lecture_id,
            user_message=message,
            assistant_message=response,
            citations=citations,
        )

        return StudentChatResponse(response=response, citations=citations)

    async def answer_global_question(
        self,
        session: AsyncSession,
        *,
        message: str,
        student_email: str,
        subject_id: str | None = None,
    ) -> StudentDoubtResponse:
        bundles = await self._validated_lecture_bundles(session)
        if subject_id:
            bundles = [bundle for bundle in bundles if self._subject_id(bundle.lecture) == subject_id]

        scope_label = (
            bundles[0].lecture.subject_name or bundles[0].lecture.subject_code or "the selected subject"
            if subject_id and bundles
            else "the approved knowledge base"
        )

        if self._is_small_talk(message):
            return StudentDoubtResponse(
                response=self._small_talk_response(scope_label if subject_id else None),
                citations=[],
                scope_label=scope_label,
                structured_answer=None,
            )

        if not bundles:
            return StudentDoubtResponse(
                response=f"I could not find any approved study material in {scope_label} yet.",
                citations=[],
                scope_label=scope_label,
                structured_answer=None,
            )

        lecture_lookup = {bundle.lecture.id: bundle.lecture for bundle in bundles}
        search_results = await self.knowledge_service.search(
            session,
            query=message,
            limit=max(10, min(len(bundles) * 2, 18)),
            approved_only=True,
        )
        lecture_chunks = [
            chunk
            for chunk in search_results
            if isinstance(chunk, KnowledgeChunk) and chunk.lecture_id in lecture_lookup
        ][:4]

        if not lecture_chunks:
            return StudentDoubtResponse(
                response=f"I could not find enough approved study context in {scope_label} for that doubt yet.",
                citations=[],
                scope_label=scope_label,
                structured_answer=None,
            )

        citations = [
            StudentChatCitation(
                topic=chunk.topic,
                source=(
                    f"{lecture_lookup[chunk.lecture_id].lecture_name} • Reference"
                    if str(chunk.details.get("kind", "")).startswith("reference_")
                    else lecture_lookup[chunk.lecture_id].lecture_name
                ),
                excerpt=chunk.content[:280].strip(),
            )
            for chunk in lecture_chunks
        ]

        answer_payload = None
        if self.openrouter.is_configured:
            try:
                answer_payload = self.openrouter.answer_student_doubt(
                    question=message,
                    subject_context=scope_label,
                    context_items=[
                        {
                            "topic": chunk.topic,
                            "source": citations[index].source,
                            "content": chunk.content[:900],
                        }
                        for index, chunk in enumerate(lecture_chunks)
                    ],
                )
            except Exception:
                answer_payload = None

        if answer_payload is None:
            response = (
                f"Here is the clearest answer I found in {scope_label}: {citations[0].excerpt}"
                if citations
                else f"I could not find enough approved study context in {scope_label} for that doubt yet."
            )
            structured_answer = self._fallback_structured_answer(response)
        else:
            response = str(answer_payload.get("answer", "")).strip() or (
                f"Here is the clearest answer I found in {scope_label}: {citations[0].excerpt}"
                if citations
                else f"I could not find enough approved study context in {scope_label} for that doubt yet."
            )
            raw_structured = answer_payload.get("structured_answer")
            structured_answer = (
                StructuredStudyAnswer.model_validate(raw_structured)
                if isinstance(raw_structured, dict) and self._should_emit_structured_answer(message)
                else None
            )

        await self.persistence.append_chat_exchange(
            session,
            student_email=student_email,
            lecture_id=lecture_chunks[0].lecture_id,
            user_message=message,
            assistant_message=response,
            citations=citations,
        )

        return StudentDoubtResponse(
            response=response,
            citations=citations,
            scope_label=scope_label,
            structured_answer=structured_answer,
        )

    async def get_practice_questions(self, session: AsyncSession, limit: int = 6) -> list[StudentPracticeQuestion]:
        bundles = await self._validated_lecture_bundles(session)
        questions: list[StudentPracticeQuestion] = []

        eligible_bundles = [
            bundle
            for bundle in bundles
            if self._knowledge_topics(bundle.lecture)
        ]

        for bundle in eligible_bundles[:limit]:
            lecture = bundle.lecture
            topic_titles = [topic.title for topic in self._knowledge_topics(lecture)]
            primary_topic = topic_titles[0] if topic_titles else (lecture.subject_name or lecture.lecture_name)
            distractors = (topic_titles[1:4] or [lecture.subject_code or "Core concept", "Worked example", "Revision note"])[:3]
            while len(distractors) < 3:
                distractors.append(f"Related concept {len(distractors) + 1}")
            options = [primary_topic, *distractors[:3]]

            questions.append(
                StudentPracticeQuestion(
                    id=str(uuid.uuid4()),
                    lecture_id=lecture.id,
                    lecture_name=lecture.lecture_name,
                    subject_id=self._subject_id(lecture),
                    subject_name=lecture.subject_name,
                    subject_code=lecture.subject_code,
                    question=f"Which topic is the strongest anchor for revising {lecture.lecture_name}?",
                    options=options,
                    answer=0,
                    explanation=self._lecture_summary(lecture),
                )
            )

        return questions[:limit]
