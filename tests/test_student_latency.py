"""Regression coverage for fast and safe student doubt retrieval."""
import asyncio
import time
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.models.knowledge import KnowledgeChunk
from app.services.student_portal import StudentPortalService, ValidatedLectureBundle
from app.services.knowledge import KnowledgeService


def fixture_service():
    svc = StudentPortalService()
    lecture_id = uuid.uuid4()
    lecture = SimpleNamespace(
        id=lecture_id, subject_key="cs-101", subject_name="Algorithms",
        subject_code="CS101", lecture_name="Sorting algorithms",
    )
    svc._validated_lecture_bundles = AsyncMock(return_value=[
        ValidatedLectureBundle(lecture=lecture, false_claim_count=0, reference_count=0, topic_count=1)
    ])
    return svc, lecture_id


@pytest.mark.asyncio
async def test_global_doubt_uses_light_eligibility_and_allowlist():
    svc, lid = fixture_service()
    svc.knowledge_service.search = AsyncMock(return_value=[])
    svc.openrouter.api_key = ""
    result = await svc.answer_global_question(
        AsyncMock(), message="Explain quicksort", subject_id="cs-101",
        student_email="student@example.com",
    )
    assert "could not find enough approved" in result.response
    assert svc._validated_lecture_bundles.await_args.kwargs == {
        "include_reference_files": False,
        "include_topic_segments": False,
        "include_knowledge_chunks": False,
    }
    assert svc.knowledge_service.search.await_args.kwargs["lecture_ids"] == [lid]
    assert svc.knowledge_service.search.await_args.kwargs["approved_only"] is True


@pytest.mark.asyncio
async def test_doubt_sync_model_runs_without_blocking_asyncio():
    svc, lid = fixture_service()
    chunk = KnowledgeChunk(
        id=uuid.uuid4(), lecture_id=lid, topic="Sorting",
        content="Quicksort divides arrays.", details={"kind": "topic"},
        embedding=[0.0] * 384,
    )
    svc.knowledge_service.search = AsyncMock(return_value=[chunk])
    svc.persistence.append_chat_exchange = AsyncMock()
    svc.openrouter.api_key = "test-key"

    def slow_provider(**kwargs):
        time.sleep(0.15)
        return {"answer": "Quicksort partitions an array."}

    svc.openrouter.answer_student_doubt = slow_provider

    async def heartbeat():
        await asyncio.sleep(0.025)
        return True

    response, responsive = await asyncio.gather(
        svc.answer_global_question(AsyncMock(), message="Explain quicksort",
                                   student_email="student@example.com"),
        asyncio.wait_for(heartbeat(), timeout=0.10),
    )
    assert responsive
    assert "Quicksort partitions" in response.response
    svc.persistence.append_chat_exchange.assert_awaited_once()


@pytest.mark.asyncio
async def test_empty_allowlist_skips_embedding_generation():
    svc = KnowledgeService()
    svc.embedding_service.encode = lambda _: (_ for _ in ()).throw(AssertionError("embedding must be skipped"))
    assert await svc.search(AsyncMock(), "hello", lecture_ids=[]) == []
