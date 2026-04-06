# EduSense AI Backend

FastAPI backend for a campus lecture validation and knowledge-pipeline admin portal. The service ingests uploaded lecture media, transcribes it with Deepgram, structures transcript data, runs a fact-check workflow, stores searchable knowledge embeddings in PostgreSQL with pgvector, and exposes analytics-oriented endpoints for the admin UI.

## Stack

- FastAPI
- Supabase Postgres + pgvector
- SQLAlchemy async
- Deepgram for transcription
- OpenAI-compatible embeddings API for production search vectors
- Optional Sentence Transformers fallback for local development
- FFmpeg for video-to-audio extraction

## Run locally

1. Copy `.env.example` to `.env` and fill your Supabase database URL, service role key, and storage buckets.
2. Install dependencies:

```bash
pip install -r requirements.dev.txt
```

3. Run database migrations:

```bash
alembic upgrade head
```

4. Start the API:

```bash
uvicorn app.main:app --reload
```

The API is available at `http://localhost:8000`, and interactive docs are at `http://localhost:8000/docs`.

## Database migrations

This project now includes Alembic for proper Supabase/PostgreSQL schema migrations.

For a fresh database:

```bash
alembic upgrade head
```

If you already have an older local database created by the previous startup `create_all` flow, stamp it once so Alembic starts tracking the current schema without trying to recreate tables:

```bash
alembic stamp head
```

After that, future schema changes should go through Alembic revisions instead of ad-hoc startup alters.

## Supabase storage

The backend can now use Supabase Storage for uploaded lecture files and reference documents while still keeping a local cached copy for processing.

Set these in `.env`:

```bash
STORAGE_BACKEND=supabase
SUPABASE_URL=https://<project-ref>.supabase.co
SUPABASE_SERVICE_ROLE_KEY=<service-role-key>
SUPABASE_LECTURE_BUCKET=lecture-content
SUPABASE_REFERENCE_BUCKET=reference-content
```

The local `storage/uploads` directory is still used as a processing cache for Deepgram preprocessing and rebuild flows.

## Embeddings

Production deploys can avoid the heavy local transformer stack by using an OpenAI-compatible embeddings API. If you already use OpenRouter, the backend can reuse that key directly.

Set these in `.env` for Render:

```bash
EMBEDDING_PROVIDER=openrouter
OPENROUTER_API_KEY=<your-openrouter-key>
EMBEDDING_API_URL=https://openrouter.ai/api/v1/embeddings
EMBEDDING_API_MODEL=text-embedding-3-small
VECTOR_SIZE=384
```

You can also set `EMBEDDING_API_KEY` explicitly, but when `EMBEDDING_PROVIDER=openrouter` the backend will reuse `OPENROUTER_API_KEY` automatically.

If you want to keep local embeddings for development, install the optional local AI dependencies:

```bash
pip install -r requirements.local-ai.txt
```

Then switch back to:

```bash
EMBEDDING_PROVIDER=local
EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2
VECTOR_SIZE=384
```

## API surface

- `GET /api/v1/dashboard`
- `POST /api/v1/upload`
- `GET /api/v1/processing`
- `GET /api/v1/processing/{lecture_id}`
- `GET /api/v1/lecture/{lecture_id}`
- `GET /api/v1/fact-check/{lecture_id}`
- `POST /api/v1/fact-check/update`
- `GET /api/v1/knowledge?query=...`
- `GET /api/v1/analytics`

## Notes

- `ffmpeg` must be installed and available on `PATH` for video uploads.
- Production runtime no longer requires `sentence-transformers` unless you explicitly use `EMBEDDING_PROVIDER=local`.
- Set `AUTO_BOOTSTRAP_SCHEMA=false` for Supabase deployments so schema changes are controlled by Alembic only.
- `docker-compose.yml` is now just an optional local fallback for development, not the primary deployment path.
- The production Docker image installs the lean runtime set from `requirements.txt`; migration/local tooling stays in `requirements.dev.txt`.
