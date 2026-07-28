import hashlib
import math
import re
from typing import Protocol

import httpx

from app.core.config import Settings, settings


_TOKEN_PATTERN = re.compile(r"[\w]+", re.UNICODE)


class EmbeddingProvider(Protocol):
    async def embed_documents(
        self,
        texts: list[str],
    ) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...


class HashEmbeddingProvider:
    def __init__(self, dimensions: int = 256):
        if dimensions < 1:
            raise ValueError("dimensions must be positive")
        self.dimensions = dimensions

    async def embed_documents(
        self,
        texts: list[str],
    ) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        tokens = _TOKEN_PATTERN.findall(text.casefold())
        if not tokens:
            tokens = [text.casefold() or "<empty>"]
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:8], "big") % self.dimensions
            sign = 1.0 if digest[8] & 1 else -1.0
            weight = 1.0 + digest[9] / 255.0
            vector[index] += sign * weight
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            fallback = hashlib.sha256(text.encode("utf-8")).digest()
            vector[int.from_bytes(fallback[:8], "big") % self.dimensions] = 1.0
            norm = 1.0
        return [value / norm for value in vector]


class OpenAICompatibleEmbeddingProvider:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        dimensions: int,
        timeout_seconds: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        if not base_url:
            raise ValueError("embedding_base_url is required")
        if not api_key:
            raise ValueError("embedding_api_key is required")
        if dimensions < 1:
            raise ValueError("dimensions must be positive")
        self.endpoint = f"{base_url.rstrip('/')}/embeddings"
        self._api_key = api_key
        self.model = model
        self.dimensions = dimensions
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    async def embed_documents(
        self,
        texts: list[str],
    ) -> list[list[float]]:
        if not texts:
            return []
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                transport=self.transport,
            ) as client:
                response = await client.post(
                    self.endpoint,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json={"model": self.model, "input": texts},
                )
        except httpx.TimeoutException as exc:
            raise RuntimeError("Embedding request timed out") from exc
        except httpx.HTTPError as exc:
            raise RuntimeError("Embedding request failed") from exc

        if response.status_code == 401:
            raise RuntimeError("Embedding request was unauthorized")
        if response.status_code == 429:
            raise RuntimeError("Embedding request was rate limited")
        if response.status_code >= 500:
            raise RuntimeError("Embedding service failed")
        if response.status_code >= 400:
            raise RuntimeError(
                f"Embedding request failed with status {response.status_code}"
            )
        try:
            payload = response.json()
            data = payload["data"]
            ordered = sorted(data, key=lambda item: item["index"])
            embeddings = [item["embedding"] for item in ordered]
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("Embedding response was malformed") from exc

        if len(embeddings) != len(texts):
            raise RuntimeError("Embedding response count mismatch")
        if any(
            not isinstance(vector, list)
            or len(vector) != self.dimensions
            or any(not isinstance(value, (int, float)) for value in vector)
            for vector in embeddings
        ):
            raise RuntimeError("Embedding response dimension mismatch")
        return [
            [float(value) for value in vector]
            for vector in embeddings
        ]

    async def embed_query(self, text: str) -> list[float]:
        return (await self.embed_documents([text]))[0]


def create_embedding_provider(
    config: Settings = settings,
) -> EmbeddingProvider:
    if config.embedding_provider == "hash":
        return HashEmbeddingProvider(config.embedding_dimensions)
    if config.embedding_provider == "openai_compatible":
        return OpenAICompatibleEmbeddingProvider(
            base_url=config.embedding_base_url or "",
            api_key=config.embedding_api_key or "",
            model=config.embedding_model,
            dimensions=config.embedding_dimensions,
        )
    raise ValueError(
        f"Unsupported embedding provider: {config.embedding_provider}"
    )
