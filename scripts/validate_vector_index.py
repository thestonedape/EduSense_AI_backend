"""Compare exact vs HNSW on copied vectors; enable live index only explicitly.
No lecture text leaves PostgreSQL. Requires a representative existing corpus.
"""
import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
import numpy as np
from sqlalchemy import select, text
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root))
from app.core.config import get_settings
from app.db.session import engine
from app.models.knowledge import KnowledgeChunk

parser=argparse.ArgumentParser();parser.add_argument('--enable',action='store_true');parser.add_argument('--output',type=Path,default=Path('artifacts/hnsw-verification.json'));args=parser.parse_args()

async def run():
    dimension=get_settings().vector_size
    if not 1<=dimension<=2000:raise ValueError('HNSW vector dimension must be 1..2000')
    report={'enabled':False,'dimension':dimension,'distance':'cosine','queries':50,'minimum_recall_at_10':.99}
    async with engine.begin() as db:
        rows=(await db.execute(select(KnowledgeChunk.id,KnowledgeChunk.embedding,KnowledgeChunk.lecture_id).order_by(KnowledgeChunk.id).limit(10000))).all()
        if len(rows)<500:raise RuntimeError('At least 500 existing vectors are required for a meaningful index check')
        if any(len(vector)!=dimension for _,vector,_ in rows):raise RuntimeError('Stored embedding dimension differs from VECTOR_SIZE')
        await db.execute(text(f'CREATE TEMP TABLE sde_vector_check (id text PRIMARY KEY, lecture_id text, embedding vector({dimension})) ON COMMIT DROP'))
        await db.execute(text('INSERT INTO sde_vector_check (id,lecture_id,embedding) VALUES (:id,:lecture_id,CAST(:vector AS vector))'),[{'id':str(i),'lecture_id':str(lecture),'vector':json.dumps(np.asarray(v).tolist())} for i,v,lecture in rows])
        await db.execute(text('CREATE INDEX sde_vector_check_hnsw ON sde_vector_check USING hnsw (embedding vector_cosine_ops) WITH (m=16,ef_construction=64)'))
        await db.execute(text('ANALYZE sde_vector_check'))
        rng=np.random.default_rng(7300930);checks=[]
        for i in rng.choice(len(rows),50,replace=False):
            vector=np.asarray(rows[i][1],dtype=np.float32)+rng.normal(0,.01,dimension).astype(np.float32)
            params={'vector':json.dumps(vector.tolist()),'lecture':str(rows[i][2])}
            for scoped in [False,True]:
                query='SELECT id FROM sde_vector_check '+('WHERE lecture_id=:lecture ' if scoped else '')+'ORDER BY embedding <=> CAST(:vector AS vector) LIMIT 10'
                await db.execute(text('SET LOCAL enable_indexscan=off'));await db.execute(text('SET LOCAL enable_bitmapscan=off'));await db.execute(text('SET LOCAL enable_seqscan=on'))
                start=time.perf_counter();exact=list((await db.execute(text(query),params)).scalars());exact_ms=(time.perf_counter()-start)*1000
                await db.execute(text('SET LOCAL enable_indexscan=on'));await db.execute(text('SET LOCAL enable_seqscan=off'));await db.execute(text('SET LOCAL hnsw.ef_search=200'))
                start=time.perf_counter();approx=list((await db.execute(text(query),params)).scalars());approx_ms=(time.perf_counter()-start)*1000
                checks.append({'scoped':scoped,'recall':len(set(exact)&set(approx))/len(exact),'exact_ms':exact_ms,'hnsw_ms':approx_ms})
        report.update({'vectors':len(rows),'checks':checks,'passed':all(x['recall']>=.99 for x in checks)})
    if args.enable:
        if not report['passed']:raise RuntimeError('Index failed exact-search recall gate')
        async with engine.connect() as db:
            db=await db.execution_options(isolation_level='AUTOCOMMIT')
            await db.execute(text('CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_knowledge_embedding_hnsw ON knowledge_chunks USING hnsw (embedding vector_cosine_ops) WITH (m=16,ef_construction=64)'))
        report['enabled']=True
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,indent=2))
    await engine.dispose();print(json.dumps({k:v for k,v in report.items() if k!='checks'}))

asyncio.run(run())
