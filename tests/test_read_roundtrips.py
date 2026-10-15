"""Regression tests for a one-trip student dashboard and scoped subject cards."""
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from app.models.lecture import Lecture
from app.models.student import StudentLectureStatus
from app.services.student_persistence import StudentPersistenceService
from app.services.student_portal import StudentPortalService, ValidatedLectureBundle


def _lecture():
    return Lecture(
        id=uuid.uuid4(),
        lecture_name="Sorting",
        original_filename="sorting.wav",
        storage_path="fixture",
        course="CS",
        module="Sorting",
        subject_key="cs-sorting",
        subject_name="Sorting",
        subject_code="CS101",
        summary="Approved lecture summary",
        metrics={},
    )


@pytest.mark.asyncio
async def test_recent_only_dashboard_skips_stats_and_returns_scoped_lecture():
    svc = StudentPortalService()
    lecture = _lecture()
    svc.persistence.get_dashboard_stats = AsyncMock()
    svc.persistence.get_recent_lecture_rows = AsyncMock(return_value=[
        (lecture, StudentLectureStatus.in_progress, datetime(2026, 10, 15, tzinfo=timezone.utc), 1, 2),
    ])
    result = await svc.get_dashboard(
        AsyncMock(), student_email="student@example.com", include_stats=False,
    )
    svc.persistence.get_dashboard_stats.assert_not_awaited()
    svc.persistence.get_recent_lecture_rows.assert_awaited_once()
    assert svc.persistence.get_recent_lecture_rows.await_args.kwargs["student_email"] == "student@example.com"
    assert result.stats.tracked_lectures == 0
    assert len(result.recent_lectures) == 1
    assert result.recent_lectures[0].summary == "Approved lecture summary"
    assert result.recent_lectures[0].subject_id == "cs-sorting"


@pytest.mark.asyncio
async def test_dashboard_statistics_remain_available_by_default():
    svc = StudentPortalService()
    svc.persistence.get_dashboard_stats = AsyncMock(return_value={
        "tracked_lectures": 3,
        "completed_lectures": 1,
        "quiz_attempts": 2,
        "saved_chats": 1,
    })
    svc.persistence.get_recent_lecture_rows = AsyncMock(return_value=[])
    result = await svc.get_dashboard(AsyncMock(), student_email="student@example.com")
    assert result.stats.tracked_lectures == 3
    assert result.stats.quiz_attempts == 2
    svc.persistence.get_dashboard_stats.assert_awaited_once()


@pytest.mark.asyncio
async def test_recent_query_filters_to_user_and_validated_lectures():
    svc = StudentPersistenceService()
    session = AsyncMock()
    session.execute.return_value = MagicMock()
    session.execute.return_value.all.return_value = []
    rows = await svc.get_recent_lecture_rows(session, student_email="student@example.com")
    assert rows == []
    compiled = session.execute.await_args.args[0].compile(dialect=postgresql.dialect())
    statement = str(compiled)
    assert "student_lecture_progress.user_email" in statement
    assert "lectures.status" in statement
    assert "claims.verdict" in statement
    assert "NOT (EXISTS" in statement
    assert list(compiled.params.values()).count("student@example.com") == 1


@pytest.mark.asyncio
async def test_subject_page_fetches_one_scoped_bundle_without_chunks():
    svc = StudentPortalService()
    lecture = _lecture()
    svc._validated_lecture_bundles = AsyncMock(return_value=[
        ValidatedLectureBundle(
            lecture=lecture, false_claim_count=0,
            reference_count=0, topic_count=2,
        )
    ])
    session = AsyncMock()
    result = await svc.get_subject(session, subject_id="cs-sorting")
    assert result is not None
    assert result.subject.lecture_count == 1
    assert result.lectures[0].summary == "Approved lecture summary"
    svc._validated_lecture_bundles.assert_awaited_once_with(
        session,
        subject_key="cs-sorting",
        include_reference_files=False,
        include_topic_segments=False,
        include_knowledge_chunks=False,
    )
