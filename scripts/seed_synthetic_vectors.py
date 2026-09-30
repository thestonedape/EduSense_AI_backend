"""Seed/remove a clustered synthetic embedding corpus in a *_test database for index checks.

    python scripts/seed_synthetic_vectors.py --chunks 5000 --lectures 20
    python scripts/validate_vector_index.py --output artifacts/hnsw-synthetic.json
    python scripts/seed_synthetic_vectors.py --remove

Synthetic vectors verify index mechanics only; real recall must be checked on the real corpus.
"""
import argparse
import asyncio
import sys
from pathlib import Path
import numpy as np
from sqlalchemy import delete
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.config import get_settings
from app.db.session import SessionLocal, engine
from app.models.knowledge import KnowledgeChunk
from app.models.lecture import Lecture

parser = argparse.ArgumentParser()
parser.add_argument('--chunks', type=int, default=5000)
parser.add_argument('--lectures', type=int, default=20)
parser.add_argument('--remove', action='store_true')
args = parser.parse_args()


async def main():
    if not engine.url.database.endswith('_test'):
        raise SystemExit('requires a *_test database')
    async with SessionLocal() as db:
        if args.remove:
            await db.execute(delete(Lecture).where(Lecture.course == 'synthetic-vectors'))
        else:
            dim, rng = get_settings().vector_size, np.random.default_rng(7300930)
            centers = rng.normal(size=(64, dim))  # topic clusters, like related lecture chunks
            lectures = [Lecture(lecture_name=f'synthetic-{i}', original_filename='x.wav', storage_path='unused',
                                course='synthetic-vectors', module='m', metrics={}) for i in range(args.lectures)]
            db.add_all(lectures)
            await db.flush()
            for n in range(args.chunks):
                v = centers[n % 64] + rng.normal(scale=0.6, size=dim)
                db.add(KnowledgeChunk(lecture_id=lectures[n % args.lectures].id, topic='t', content='synthetic',
                                      details={}, embedding=(v / np.linalg.norm(v)).tolist()))
        await db.commit()
    await engine.dispose()


asyncio.run(main())
