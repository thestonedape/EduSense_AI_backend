import logging

from sqlalchemy import text

from app.core.config import get_settings
from app.db.base import Base
from app import models  # noqa: F401
from app.db.session import engine


logger = logging.getLogger("app.db")
settings = get_settings()


async def initialize_database() -> None:
    async with engine.begin() as conn:
        try:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        except Exception as exc:
            logger.warning("vector_extension_bootstrap_skipped error=%s", exc)

        if settings.auto_bootstrap_schema:
            await conn.run_sync(Base.metadata.create_all)
