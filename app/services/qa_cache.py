"""Opaque, identity-scoped keys. Cache failures never fail Q&A."""
import hashlib
import json
import os
from redis.asyncio import Redis
from sqlalchemy import text
from app.core.config import get_settings

async def cache_key(session, lecture_id, email, question):
    version = await session.scalar(text("SELECT md5(coalesce(string_agg(id::text || content || metadata::text, '' ORDER BY id), '')) FROM knowledge_chunks WHERE lecture_id = :lecture_id AND metadata->>'student_visible' = 'true'"), {'lecture_id':lecture_id})
    settings = get_settings()
    scope = ['qa-v1', str(lecture_id), email.lower(), version, settings.openrouter_model, settings.embedding_api_model, question.strip()]
    return 'edusense:qa:' + hashlib.sha256(json.dumps(scope, separators=(',',':')).encode()).hexdigest()

async def read(key):
    try:
        async with Redis.from_url(os.getenv('REDIS_URL','redis://localhost:6379/0'), socket_connect_timeout=2, socket_timeout=2) as redis:
            value = await redis.get(key)
            return json.loads(value) if value else None
    except Exception:
        return None

async def write(key, answer):
    try:
        async with Redis.from_url(os.getenv('REDIS_URL','redis://localhost:6379/0'), socket_connect_timeout=2, socket_timeout=2) as redis:
            await redis.set(key, json.dumps(answer), ex=300)
    except Exception:
        pass
