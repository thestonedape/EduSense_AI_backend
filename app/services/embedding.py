from functools import lru_cache

from sentence_transformers import SentenceTransformer

from app.core.config import get_settings


settings = get_settings()


@lru_cache
def get_embedding_model() -> SentenceTransformer:
    return SentenceTransformer(settings.embedding_model)


class EmbeddingService:
    def __init__(self) -> None:
        self.model = get_embedding_model()

    def encode(self, texts: list[str]) -> list[list[float]]:
        embeddings = self.model.encode(texts, normalize_embeddings=True)
        return [embedding.tolist() for embedding in embeddings]
