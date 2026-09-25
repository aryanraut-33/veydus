# ─────────────────────────────────────────────────────────────────
# VEYDUS — NVIDIA NIM Inference Provider
# ─────────────────────────────────────────────────────────────────
# What:  Adapter for NVIDIA NIM cloud/hosted endpoints (embeddings & LLMs).
# How:   Uses httpx.AsyncClient for asynchronous HTTP requests, tenacity
#        for exponential backoff with jitter on HTTP 429 and 503/504 errors,
#        and numpy for Matryoshka dimension truncation and L2 unit-normalization.
# Why:   HLD §7.2 & §12.2 (ADR-0004) require embedding with nvidia/llama-nemotron-embed-1b-v2
#        truncated to 768 dimensions and L2-normalized. The truncation MUST
#        precede L2-normalization to preserve unit length for cosine similarity.
# Tools: httpx (AsyncClient), tenacity (retry, wait_random_exponential), numpy (L2 norm).
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import httpx
import numpy as np
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_random_exponential,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)


def apply_matryoshka_truncation(
    vector: list[float] | np.ndarray, target_dim: int = 768
) -> list[float]:
    """Truncate embedding to target_dim and then L2-normalize.

    CRITICAL INVARIANT (HLD §12.2, ADR-0004):
    Order of operations: Truncate FIRST (2048 -> 768), then L2-normalize.
    Reversing the order (normalizing first, then truncating) causes the resulting
    vector to have norm < 1.0, degrading cosine distance in pgvector HNSW index.
    """
    arr = np.asarray(vector, dtype=np.float32)
    truncated = arr[:target_dim]
    norm = float(np.linalg.norm(truncated))
    normalized = truncated / norm if norm > 0.0 else truncated
    return [float(x) for x in normalized]


def _is_retryable_exception(exc: BaseException) -> bool:
    """Check if exception is a rate limit or transient network error."""
    if isinstance(exc, httpx.HTTPStatusError):
        # 429 Too Many Requests, 502 Bad Gateway, 503 Service Unavailable, 504 Gateway Timeout
        return exc.response.status_code in {429, 502, 503, 504}
    return isinstance(exc, (httpx.TimeoutException, httpx.NetworkError))


class NIMProvider:
    """NVIDIA NIM inference provider for embeddings and text generation."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://integrate.api.nvidia.com/v1",
        embedding_model: str = "nvidia/llama-nemotron-embed-1b-v2",
        dimensions: int = 768,
        generation_model: str = "meta/llama-3.1-70b-instruct",
        timeout: float = 60.0,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.embedding_model = embedding_model
        self.dimensions = dimensions
        self.generation_model = generation_model
        self.timeout = timeout

    def _get_headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    @retry(
        retry=retry_if_exception(_is_retryable_exception),
        wait=wait_random_exponential(multiplier=1, max=10),
        stop=stop_after_attempt(5),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    async def _post_with_retry(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Send POST request with exponential backoff and jitter."""
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(url, json=payload, headers=self._get_headers())
            response.raise_for_status()
            return response.json()  # type: ignore[no-any-return]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Generate dense vector embeddings with Matryoshka truncation and normalization."""
        if not texts:
            return []

        payload: dict[str, Any] = {
            "input": texts,
            "model": self.embedding_model,
            "encoding_format": "float",
            "input_type": "passage",
        }

        data = await self._post_with_retry("embeddings", payload)
        raw_items = data.get("data", [])
        # Sort by index if present to maintain order
        sorted_items = sorted(raw_items, key=lambda x: x.get("index", 0))

        embeddings: list[list[float]] = []
        for item in sorted_items:
            raw_vec = item.get("embedding", [])
            # Enforce Matryoshka truncation & L2 normalization invariant
            final_vec = apply_matryoshka_truncation(raw_vec, self.dimensions)
            embeddings.append(final_vec)

        return embeddings

    async def generate(self, prompt: str, system_prompt: str | None = None) -> str:
        """Generate completion using chat completions API."""
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self.generation_model,
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": 1024,
        }

        data = await self._post_with_retry("chat/completions", payload)
        choices = data.get("choices", [])
        if not choices:
            return ""
        return str(choices[0].get("message", {}).get("content", ""))

    async def generate_stream(
        self, prompt: str, system_prompt: str | None = None
    ) -> AsyncIterator[str]:
        """Stream completion tokens using SSE."""
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self.generation_model,
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": 1024,
            "stream": True,
        }

        url = f"{self.base_url}/chat/completions"
        async with (
            httpx.AsyncClient(timeout=self.timeout) as client,
            client.stream("POST", url, json=payload, headers=self._get_headers()) as response,
        ):
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    data_str = line[6:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        import json

                        chunk = json.loads(data_str)
                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            yield content
                    except Exception:
                        continue
