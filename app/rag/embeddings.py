import hashlib
import math
import re
from typing import Protocol

import httpx

from app.core.config import Settings, settings


_TOKEN_PATTERN = re.compile(r"[\w]+", re.UNICODE)


"""
EmbeddingProvider 是一个协议（Protocol），定义了嵌入提供者的接口。它包含两个异步方法：
1. embed_documents(texts: list[str]) -> list[list[float]]: 接收一组文本，并返回对应的嵌入向量列表。
2. embed_query(text: str) -> list[float]: 接收一个查询文本，并返回对应的嵌入向量。
任何实现了这个协议的类都可以作为嵌入提供者使用，例如 HashEmbeddingProvider 或 OpenAICompatibleEmbeddingProvider。
"""
class EmbeddingProvider(Protocol):
    async def embed_documents(
        self,
        texts: list[str],
    ) -> list[list[float]]: ... # ... 代表该方法的具体实现将在实现类中定义

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
    # _emebd 方法是 HashEmbeddingProvider 的核心实现，用于将输入文本转换为嵌入向量。它的工作原理如下：
    # 1. 初始化一个长度为 dimensions 的零向量。
    # 2. 使用正则表达式将输入文本分割为单词（tokens）。
    # 3. 对于每个 token，计算其 SHA-256 哈希值，并根据哈希值的前几个字节确定向量的索引、符号和权重。
    # 4. 将计算得到的值累加到向量的对应索引位置。
    # 5. 对向量进行归一化处理，使其长度为 1。
    # 6. 如果向量的长度为零（即没有有效的 token），则使用整个文本的哈希值设置一个 fallback 索引为 1.0。
    # 7. 返回归一化后的向量。
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


"""
OpenAICompatibleEmbeddingProvider 是一个实现了 EmbeddingProvider 协议的类，用于与 OpenAI 兼容的嵌入服务进行交互。它的主要功能包括：
1. 初始化时接收 base_url、api_key、model、dimensions 等参数，并进行验证。
2. embed_documents 方法：接收一组文本，向指定的嵌入服务发送请求，并返回对应的嵌入向量列表。它处理请求超时、HTTP 错误、响应格式错误等情况，并确保返回的嵌入向量与输入文本数量和维度匹配。
3. embed_query 方法：接收一个查询文本，调用 embed_documents 方法并返回第一个嵌入向量。
"""
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
            # 使用 httpx 异步客户端发送 POST 请求到嵌入服务的 /embeddings 端点，传递模型名称和文本列表作为 JSON 数据。
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
