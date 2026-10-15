"""Best-effort short-lived cache for *shared* student subject summaries.

Never cache user-specific responses or full knowledge chunks here. A cache outage
must not delay or fail a student page; writes expire quickly and admin changes
also invalidate the key after the database transaction commits.
"""
import json
import logging
import os
import time

from redis.asyncio import Redis
from app.schemas.student import StudentSubjectSummary

logger = logging.getLogger("app.catalog_cache")
_KEY = "edusense:student:subjects:v1"
_TTL_SECONDS = 10
_client: Redis | None = None
_retry_after = 0.0


def _redis() -> Redis | None:
    global _client
    if not os.getenv("REDIS_URL") or time.monotonic() < _retry_after:
        return None
    if _client is None:
        _client = Redis.from_url(
            os.environ["REDIS_URL"],
            socket_connect_timeout=0.2,
            socket_timeout=0.2,
            retry_on_timeout=False,
        )
    return _client


def _failure() -> None:
    global _client, _retry_after
    _client = None
    _retry_after = time.monotonic() + 20.0


async def read_subjects() -> list[StudentSubjectSummary] | None:
    client = _redis()
    if client is None:
        return None
    try:
        raw = await client.get(_KEY)
        if raw is None:
            return None
        values = json.loads(raw)
        return [StudentSubjectSummary.model_validate(value) for value in values]
    except Exception:
        logger.warning("subject_summary_cache_read_failed", exc_info=True)
        _failure()
        return None


async def write_subjects(subjects: list[StudentSubjectSummary]) -> None:
    client = _redis()
    if client is None:
        return
    try:
        await client.set(
            _KEY,
            json.dumps([value.model_dump(mode="json") for value in subjects]),
            ex=_TTL_SECONDS,
        )
    except Exception:
        logger.warning("subject_summary_cache_write_failed", exc_info=True)
        _failure()


async def invalidate_subjects() -> None:
    client = _redis()
    if client is None:
        return
    try:
        await client.delete(_KEY)
    except Exception:
        logger.warning("subject_summary_cache_invalidation_failed", exc_info=True)
        _failure()
