"""Backend-only tables remain inaccessible even when a browser role has SQL grants."""
import asyncio
import os
import uuid
import pytest

pytestmark = pytest.mark.skipif(not os.getenv('SDE_TEST_DATABASE_URL'), reason='Explicit isolated test database required')

def test_browser_role_cannot_read_application_rows():
    async def scenario():
        from sqlalchemy import text, select
        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
        from app.models.lecture import Lecture
        url = os.environ['SDE_TEST_DATABASE_URL']
        engine = create_async_engine(url)
        assert engine.url.database.endswith('_test')
        role = 'rls_check_' + uuid.uuid4().hex
        try:
            async with engine.connect() as connection:
                transaction = await connection.begin()
                try:
                    insecure = await connection.scalar(text(
                        "SELECT count(*) FROM pg_tables WHERE schemaname='public' AND NOT rowsecurity"
                    ))
                    assert insecure == 0
                    await connection.execute(text(f'CREATE ROLE {role} NOLOGIN'))
                    await connection.execute(text(f'GRANT USAGE ON SCHEMA public TO {role}'))
                    await connection.execute(text(f'GRANT SELECT ON ALL TABLES IN SCHEMA public TO {role}'))
                    async with AsyncSession(bind=connection) as session:
                        lecture = Lecture(lecture_name='RLS fixture', original_filename='fixture.wav',
                                          storage_path='unused-fixture', course='test', module='test', metrics={})
                        session.add(lecture)
                        await session.flush()
                        assert await session.scalar(select(Lecture.id).where(Lecture.id == lecture.id)) == lecture.id
                        await connection.execute(text('SELECT set_config(\'role\', :role, true)'), {'role': role})
                        assert await connection.scalar(text('SELECT count(*) FROM public.lectures')) == 0
                        assert await connection.scalar(text('SELECT count(*) FROM public.alembic_version')) == 0
                finally:
                    await transaction.rollback()
        finally:
            await engine.dispose()
    asyncio.run(scenario())
