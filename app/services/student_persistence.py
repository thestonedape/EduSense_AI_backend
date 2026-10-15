from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.claim import Claim, ClaimVerdict
from app.models.lecture import Lecture, LectureStatus
from app.models.reference_file import ReferenceFile
from app.models.transcript import TopicSegment

from app.models.student import (
    ChatMessageRole,
    StudentChatMessage,
    StudentChatSession,
    StudentLectureProgress,
    StudentLectureStatus,
    StudentQuizAttempt,
)
from app.schemas.student import StudentChatCitation, StudentLectureSummary


class StudentPersistenceService:
    async def mark_lecture_open(
        self,
        session: AsyncSession,
        *,
        student_email: str,
        lecture_id: uuid.UUID,
        completed: bool = False,
    ) -> StudentLectureProgress:
        progress = await session.scalar(
            select(StudentLectureProgress).where(
                StudentLectureProgress.user_email == student_email,
                StudentLectureProgress.lecture_id == lecture_id,
            )
        )
        now = datetime.now(timezone.utc)
        if progress is None:
            progress = StudentLectureProgress(
                user_email=student_email,
                lecture_id=lecture_id,
                status=StudentLectureStatus.completed if completed else StudentLectureStatus.in_progress,
                open_count=1,
                last_opened_at=now,
                completed_at=now if completed else None,
            )
            session.add(progress)
            await session.flush()
            return progress

        progress.open_count += 1
        progress.last_opened_at = now
        if completed:
            progress.status = StudentLectureStatus.completed
            progress.completed_at = now
        return progress

    async def get_recent_progress(self, session: AsyncSession, *, student_email: str) -> list[StudentLectureProgress]:
        result = await session.scalars(
            select(StudentLectureProgress)
            .where(StudentLectureProgress.user_email == student_email)
            .order_by(StudentLectureProgress.last_opened_at.desc())
            .limit(6)
        )
        return list(result)

    async def get_recent_lecture_rows(
        self, session: AsyncSession, *, student_email: str,
    ) -> list:
        """One query for recently opened *validated* lectures and their counts.

        Fetch only the six most recent entries for the authenticated student.
        Do not load knowledge chunks, transcripts or other students' activity.
        The outer validation matches the student lecture endpoint's completed
        status and exclusion of lectures with false claims.
        """
        recent = (
            select(
                StudentLectureProgress.lecture_id.label("lecture_id"),
                StudentLectureProgress.status.label("progress_status"),
                StudentLectureProgress.last_opened_at.label("last_opened_at"),
            )
            .where(StudentLectureProgress.user_email == student_email)
            .order_by(StudentLectureProgress.last_opened_at.desc())
            .limit(6)
            .subquery()
        )
        reference_count = (
            select(func.count(ReferenceFile.id))
            .where(ReferenceFile.lecture_id == Lecture.id)
            .correlate(Lecture)
            .scalar_subquery()
        )
        topic_count = (
            select(func.count(TopicSegment.id))
            .where(TopicSegment.lecture_id == Lecture.id)
            .correlate(Lecture)
            .scalar_subquery()
        )
        has_false_claim = (
            select(Claim.id)
            .where(Claim.lecture_id == Lecture.id, Claim.verdict == ClaimVerdict.false)
            .correlate(Lecture)
            .exists()
        )
        rows = await session.execute(
            select(
                Lecture, recent.c.progress_status, recent.c.last_opened_at,
                reference_count, topic_count,
            )
            .join(recent, recent.c.lecture_id == Lecture.id)
            .where(Lecture.status == LectureStatus.completed)
            .where(~has_false_claim)
            .order_by(recent.c.last_opened_at.desc())
        )
        return list(rows.all())

    async def get_dashboard_stats(self, session: AsyncSession, *, student_email: str) -> dict[str, int]:
        tracked_subquery = (
            select(func.count())
            .select_from(StudentLectureProgress)
            .where(StudentLectureProgress.user_email == student_email)
            .scalar_subquery()
        )
        completed_subquery = (
            select(func.count())
            .select_from(StudentLectureProgress)
            .where(
                StudentLectureProgress.user_email == student_email,
                StudentLectureProgress.status == StudentLectureStatus.completed,
            )
            .scalar_subquery()
        )
        quiz_attempts_subquery = (
            select(func.count())
            .select_from(StudentQuizAttempt)
            .where(StudentQuizAttempt.user_email == student_email)
            .scalar_subquery()
        )
        saved_chats_subquery = (
            select(func.count())
            .select_from(StudentChatSession)
            .where(StudentChatSession.user_email == student_email)
            .scalar_subquery()
        )
        tracked, completed, quiz_attempts, saved_chats = (
            await session.execute(
                select(
                    tracked_subquery,
                    completed_subquery,
                    quiz_attempts_subquery,
                    saved_chats_subquery,
                )
            )
        ).one()
        return {
            "tracked_lectures": int(tracked or 0),
            "completed_lectures": int(completed or 0),
            "quiz_attempts": int(quiz_attempts or 0),
            "saved_chats": int(saved_chats or 0),
        }

    async def append_chat_exchange(
        self,
        session: AsyncSession,
        *,
        student_email: str,
        lecture_id: uuid.UUID,
        user_message: str,
        assistant_message: str,
        citations: list[StudentChatCitation],
    ) -> None:
        chat_session = await session.scalar(
            select(StudentChatSession)
            .where(
                StudentChatSession.user_email == student_email,
                StudentChatSession.lecture_id == lecture_id,
            )
            .order_by(StudentChatSession.updated_at.desc())
        )
        if chat_session is None:
            chat_session = StudentChatSession(user_email=student_email, lecture_id=lecture_id)
            session.add(chat_session)
            await session.flush()

        session.add(
            StudentChatMessage(
                session_id=chat_session.id,
                role=ChatMessageRole.user,
                content=user_message,
                citations=[],
            )
        )
        session.add(
            StudentChatMessage(
                session_id=chat_session.id,
                role=ChatMessageRole.assistant,
                content=assistant_message,
                citations=[citation.model_dump() for citation in citations],
            )
        )

        progress = await self.mark_lecture_open(
            session,
            student_email=student_email,
            lecture_id=lecture_id,
            completed=False,
        )
        progress.chat_count += 1

    async def record_quiz_attempt(
        self,
        session: AsyncSession,
        *,
        student_email: str,
        lecture_id: uuid.UUID,
        question_id: str,
        question: str,
        selected_answer: int,
        correct_answer: int,
        explanation: str | None,
    ) -> StudentQuizAttempt:
        attempt = StudentQuizAttempt(
            user_email=student_email,
            lecture_id=lecture_id,
            question_id=question_id,
            question=question,
            selected_answer=selected_answer,
            correct_answer=correct_answer,
            is_correct=selected_answer == correct_answer,
            explanation=explanation,
        )
        session.add(attempt)

        progress = await self.mark_lecture_open(
            session,
            student_email=student_email,
            lecture_id=lecture_id,
            completed=False,
        )
        progress.quiz_attempt_count += 1
        if attempt.is_correct:
            progress.correct_quiz_count += 1

        await session.flush()
        return attempt
