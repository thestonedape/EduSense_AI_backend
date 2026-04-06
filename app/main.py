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
    recovered_jobs = await ProcessingService().recover_orphaned_jobs()
    logging.getLogger("app.main").info("processing_recovery_complete recovered_jobs=%s", recovered_jobs)
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


@app.middleware("http")
async def request_timing_middleware(request: Request, call_next):
    started_at = perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        duration_ms = round((perf_counter() - started_at) * 1000, 1)
        logging.getLogger("app.http").exception(
            "request_failed method=%s path=%s duration_ms=%s",
            request.method,
            request.url.path,
            duration_ms,
        )
        raise

    duration_ms = round((perf_counter() - started_at) * 1000, 1)
    if duration_ms >= 250 or response.status_code >= 400:
        logger = logging.getLogger("app.http")
        log_method = logger.warning if response.status_code >= 400 else logger.info
        log_method(
            "request_timing method=%s path=%s status=%s duration_ms=%s",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )
    return response


@app.get("/health", tags=["health"])
async def health_check() -> dict[str, str]:
    return {"status": "ok"}
