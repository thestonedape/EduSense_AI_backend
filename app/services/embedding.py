from __future__ import annotations

from functools import lru_cache
from math import sqrt
from typing import TYPE_CHECKING, Any

import requests

from app.core.config import get_settings

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer


settings = get_settings()


def normalize_embedding(vector: list[float]) -> list[float]:
    magnitude = sqrt(sum(value * value for value in vector))
    if magnitude == 0:
        return vector
    return [value / magnitude for value in vector]


@lru_cache
def get_embedding_model() -> Any:
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise RuntimeError(
            "Local embedding support is not installed. Install requirements.local-ai.txt "
            "or configure EMBEDDING_PROVIDER=openai with EMBEDDING_API_KEY."
        ) from exc

    return SentenceTransformer(settings.embedding_model)


class EmbeddingService:
    def __init__(self) -> None:
        self.provider = settings.embedding_provider.strip().lower()
        self._model = None

    @property
    def model(self) -> Any:
        if self._model is None:
            self._model = get_embedding_model()
        return self._model

    def encode(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        if self.provider in {"openai", "openrouter"} and self._has_external_embedding_config():
            try:
                return self._encode_with_openai_compatible_api(texts)
            except Exception:
                if self.provider in {"openai", "openrouter"}:
                    raise

        return self._encode_locally(texts)

    def _has_external_embedding_config(self) -> bool:
        return bool(self._external_embedding_api_key().strip())

    def _encode_locally(self, texts: list[str]) -> list[list[float]]:
        embeddings = self.model.encode(texts, normalize_embeddings=True)
        return [embedding.tolist() for embedding in embeddings]

    def _external_embedding_api_key(self) -> str:
        if self.provider == "openrouter":
            return settings.embedding_api_key.strip() or settings.openrouter_api_key.strip()
        return settings.embedding_api_key.strip()

    def _external_embedding_api_url(self) -> str:
        if self.provider == "openrouter":
            custom = settings.embedding_api_url.strip()
            if custom and custom != "https://api.openai.com/v1/embeddings":
                return custom
            return "https://openrouter.ai/api/v1/embeddings"
        return settings.embedding_api_url

    def _external_embedding_headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._external_embedding_api_key()}",
            "Content-Type": "application/json",
        }
        if self.provider == "openrouter":
            if settings.openrouter_site_url.strip():
                headers["HTTP-Referer"] = settings.openrouter_site_url.strip()
            if settings.openrouter_app_name.strip():
                headers["X-Title"] = settings.openrouter_app_name.strip()
        return headers

    def _encode_with_openai_compatible_api(self, texts: list[str]) -> list[list[float]]:
        response = requests.post(
            self._external_embedding_api_url(),
            headers=self._external_embedding_headers(),
            json={
                "model": settings.embedding_api_model,
                "input": texts,
                "dimensions": settings.vector_size,
            },
            timeout=settings.embedding_timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data")
        if not isinstance(data, list):
            raise RuntimeError("Embedding API returned an invalid response payload.")

        vectors: list[list[float]] = []
        for item in data:
            embedding = item.get("embedding") if isinstance(item, dict) else None
            if not isinstance(embedding, list) or not embedding:
                raise RuntimeError("Embedding API response is missing embedding vectors.")
            vector = [float(value) for value in embedding]
            if len(vector) != settings.vector_size:
                raise RuntimeError(
                    f"Embedding size mismatch. Expected {settings.vector_size}, received {len(vector)}. "
                    "Update VECTOR_SIZE or choose a matching embedding model."
                )
            vectors.append(normalize_embedding(vector))
        return vectors
