from functools import lru_cache

from pydantic import computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "EduSense AI Backend"
    api_prefix: str = "/api/v1"
    environment: str = "development"
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/edusense"
    sync_database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/edusense"
    internal_api_key: str = ""
    upload_dir: str = "storage/uploads"
    storage_backend: str = "local"
    auto_bootstrap_schema: bool = True
    embedding_provider: str = "openai"
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_api_key: str = ""
    embedding_api_url: str = "https://api.openai.com/v1/embeddings"
    embedding_api_model: str = "text-embedding-3-small"
    embedding_timeout_seconds: int = 30
    deepgram_api_key: str = ""
    deepgram_api_url: str = "https://api.deepgram.com/v1/listen"
    deepgram_model: str = "nova-3"
    openrouter_api_key: str = ""
    openrouter_api_url: str = "https://openrouter.ai/api/v1/chat/completions"
    openrouter_model: str = "openai/gpt-4o-mini"
    openrouter_timeout_seconds: int = 20
    openrouter_site_url: str = "http://localhost:3000"
    openrouter_app_name: str = "EduSense AI"
    reference_match_threshold: float = 0.55
    supabase_url: str = ""
    supabase_service_role_key: str = ""
    supabase_lecture_bucket: str = "lecture-content"
    supabase_reference_bucket: str = "reference-content"
    ffmpeg_binary: str = "ffmpeg"
    vector_size: int = 384
    cors_origins: str = "http://localhost:3000"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @computed_field
    @property
    def cors_origins_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @computed_field
    @property
    def use_supabase_storage(self) -> bool:
        return (
            self.storage_backend.lower() == "supabase"
            and bool(self.supabase_url.strip())
            and bool(self.supabase_service_role_key.strip())
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
