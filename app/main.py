from contextlib import asynccontextmanager
import logging
from time import perf_counter

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.core.config import get_settings
from app.db.init_db import initialize_database
from app.services.processing import ProcessingService


settings = get_settings()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logging.getLogger("app.main").info("cors_origins=%s", settings.cors_origins_list)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await initialize_database()
    yield


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix=settings.api_prefix)


from app.core.observability import install_observability
install_observability(app)

@app.get("/health", tags=["health"])
async def health_check() -> dict[str, str]:
    return {"status": "ok"}

@app.get('/ready', tags=['health'])
async def readiness():
    from fastapi import HTTPException
    from sqlalchemy import text
    from arq.connections import create_pool
    from app.db.session import SessionLocal
    from app.workers.queue import WorkerSettings
    pool = None
    try:
        async with SessionLocal() as session:
            await session.execute(text('SELECT 1 FROM job_outbox LIMIT 1'))
        pool = await create_pool(WorkerSettings.redis_settings)
        await pool.ping()
        if not await pool.get('edusense:worker:ready'):
            raise RuntimeError('Worker unavailable')
        if settings.environment == 'production' and not settings.use_supabase_storage:
            raise RuntimeError('Durable storage unavailable')
        if not settings.internal_api_key:
            raise RuntimeError('Authentication unavailable')
        return {'status':'ready','queue':'redis','ledger':'postgresql'}
    except Exception:
        raise HTTPException(503, 'Processing dependencies are not ready') from None
    finally:
        if pool: await pool.aclose()
