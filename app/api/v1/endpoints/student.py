from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import db_session_dep, trusted_student_email_dep
from app.schemas.student import (
    StudentChatRequest,
    StudentChatResponse,
    StudentDashboardResponse,
    StudentDoubtRequest,
    StudentDoubtResponse,
    StudentLectureDetail,
    StudentProgressRequest,
    StudentPracticeQuestion,
    StudentPracticeResponse,
    StudentQuizAttemptRequest,
    StudentSubjectDetail,
    StudentSubjectSummary,
)
from app.services.student_portal import StudentPortalService
from app.services.student_persistence import StudentPersistenceService


router = APIRouter(prefix="/student")
service = StudentPortalService()
persistence = StudentPersistenceService()


@router.get("/dashboard", response_model=StudentDashboardResponse)
async def get_student_dashboard(
    student_email: str = Depends(trusted_student_email_dep),
    session: AsyncSession = Depends(db_session_dep),
) -> StudentDashboardResponse:
    return await service.get_dashboard(session, student_email=student_email)


@router.get("/subjects", response_model=list[StudentSubjectSummary])
async def list_student_subjects(session: AsyncSession = Depends(db_session_dep)) -> list[StudentSubjectSummary]:
    return await service.list_subjects(session)


@router.get("/subjects/{subject_id}", response_model=StudentSubjectDetail)
async def get_student_subject(subject_id: str, session: AsyncSession = Depends(db_session_dep)) -> StudentSubjectDetail:
    subject = await service.get_subject(session, subject_id)
    if subject is None:
        raise HTTPException(status_code=404, detail="Validated subject not found.")
    return subject


@router.get("/lectures/{lecture_id}", response_model=StudentLectureDetail)
async def get_student_lecture(lecture_id: UUID, session: AsyncSession = Depends(db_session_dep)) -> StudentLectureDetail:
    lecture = await service.get_lecture(session, lecture_id)
    if lecture is None:
        raise HTTPException(status_code=404, detail="Validated lecture not found.")
    return lecture


@router.post("/chat", response_model=StudentChatResponse)
async def student_chat(
    payload: StudentChatRequest,
    student_email: str = Depends(trusted_student_email_dep),
    session: AsyncSession = Depends(db_session_dep),
) -> StudentChatResponse:
    answer = await service.answer_question(
        session,
        lecture_id=payload.lecture_id,
        message=payload.message,
        student_email=student_email,
    )
    if answer is None:
        raise HTTPException(status_code=404, detail="Validated lecture not found.")
    await session.commit()
    return answer


@router.post("/doubts", response_model=StudentDoubtResponse)
async def student_doubt_solver(
    payload: StudentDoubtRequest,
    student_email: str = Depends(trusted_student_email_dep),
    session: AsyncSession = Depends(db_session_dep),
) -> StudentDoubtResponse:
    answer = await service.answer_global_question(
        session,
        message=payload.message,
        subject_id=payload.subject_id,
        student_email=student_email,
    )
    await session.commit()
    return answer


@router.get("/practice", response_model=StudentPracticeResponse)
async def student_practice(
    limit: int = Query(default=6, ge=1, le=12),
    session: AsyncSession = Depends(db_session_dep),
) -> StudentPracticeResponse:
    questions: list[StudentPracticeQuestion] = await service.get_practice_questions(session, limit=limit)
    return StudentPracticeResponse(questions=questions)


@router.post("/progress/open")
async def mark_student_progress(
    payload: StudentProgressRequest,
    student_email: str = Depends(trusted_student_email_dep),
    session: AsyncSession = Depends(db_session_dep),
) -> dict:
    await persistence.mark_lecture_open(
        session,
        student_email=student_email,
        lecture_id=payload.lecture_id,
        completed=payload.completed,
    )
    await session.commit()
    return {"success": True}


@router.post("/practice/attempt")
async def record_practice_attempt(
    payload: StudentQuizAttemptRequest,
    student_email: str = Depends(trusted_student_email_dep),
    session: AsyncSession = Depends(db_session_dep),
) -> dict:
    attempt = await persistence.record_quiz_attempt(
        session,
        student_email=student_email,
        lecture_id=payload.lecture_id,
        question_id=payload.question_id,
        question=payload.question,
        selected_answer=payload.selected_answer,
        correct_answer=payload.correct_answer,
        explanation=payload.explanation,
    )
    await session.commit()
    return {"success": True, "is_correct": attempt.is_correct}
