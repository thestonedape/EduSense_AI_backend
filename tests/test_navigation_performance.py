"""Navigation-path regressions: cache privacy and scoped database reads."""
import asyncio
import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from app.schemas.student import StudentSubjectSummary
from app.services import catalog_cache
from app.services.student_portal import StudentPortalService


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.expiry = {}

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, ex):
        self.values[key] = value
        self.expiry[key] = ex

    async def delete(self, key):
        self.values.pop(key, None)


@pytest.mark.asyncio
async def test_subject_cache_roundtrip_and_invalidation(monkeypatch):
    redis = FakeRedis()
    monkeypatch.setenv("REDIS_URL", "redis://fixture:6379/0")
    monkeypatch.setattr(catalog_cache, "_client", redis)
    monkeypatch.setattr(catalog_cache, "_retry_after", 0.0)
    subject = StudentSubjectSummary(
        id="math", name="Mathematics", code="M101",
        lecture_count=2, reference_count=1, description="Validated lectures.",
    )
    assert await catalog_cache.read_subjects() is None
    await catalog_cache.write_subjects([subject])
    assert redis.expiry[catalog_cache._KEY] <= 10
    assert (await catalog_cache.read_subjects())[0] == subject
    await catalog_cache.invalidate_subjects()
    assert await catalog_cache.read_subjects() is None


@pytest.mark.asyncio
async def test_subject_cache_disabled_without_redis(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setattr(catalog_cache, "_client", None)
    monkeypatch.setattr(catalog_cache, "_retry_after", 0.0)
    assert await catalog_cache.read_subjects() is None
    await catalog_cache.write_subjects([])


@pytest.mark.asyncio
async def test_single_lecture_does_not_scan_all_lecture_bundles():
    svc = StudentPortalService()
    svc._validated_lecture_bundles = AsyncMock(return_value=[])
    lecture_id = uuid.uuid4()
    assert await svc.get_lecture(AsyncMock(), lecture_id) is None
    assert svc._validated_lecture_bundles.await_args.kwargs == {"lecture_ids": [lecture_id]}


@pytest.mark.asyncio
async def test_empty_lecture_id_list_short_circuits_database():
    svc = StudentPortalService()
    session = AsyncMock()
    assert await svc._validated_lecture_bundles(session, lecture_ids=[]) == []
    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_scoped_aggregations_use_lecture_ids():
    svc = StudentPortalService()
    session = AsyncMock()
    session.execute.return_value.all.return_value = []
    target = uuid.uuid4()
    result = await svc._validated_lecture_bundles(
        session, lecture_ids=[target], include_reference_files=False,
        include_topic_segments=False, include_knowledge_chunks=False,
    )
    assert result == []
    stmt = session.execute.await_args.args[0]
    sql = str(stmt.compile(dialect=postgresql.dialect()))
    # Count subqueries AND the final lecture query are scoped.
    assert sql.count("IN (__[POSTCOMPILE_") >= 4
