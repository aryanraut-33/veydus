# ─────────────────────────────────────────────────────────────────
# VEYDUS — Unit Tests: NVIDIA NIM Retry & Backoff Logic
# ─────────────────────────────────────────────────────────────────
# What:  Validates exponential backoff retry behavior on HTTP 429 and transient errors.
# How:   Mocks httpx.AsyncClient responses with status code sequences
#        (e.g., 429 -> 429 -> 200) using unittest.mock.
# Why:   HLD §7.2 states that HTTP 429 is expected during bursts against NIM endpoints.
#        The adapter must retry transparently before bubbling failure.
# Tools: pytest, unittest.mock, httpx, tenacity.
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from veydus.providers.nim import NIMProvider, _is_retryable_exception


def test_is_retryable_exception() -> None:
    """Check classification of retryable exceptions."""
    req = httpx.Request("POST", "https://integrate.api.nvidia.com/v1/embeddings")

    resp_429 = httpx.Response(status_code=429, request=req)
    assert _is_retryable_exception(
        httpx.HTTPStatusError("Rate limit", request=req, response=resp_429)
    )

    resp_503 = httpx.Response(status_code=503, request=req)
    assert _is_retryable_exception(
        httpx.HTTPStatusError("Service unavailable", request=req, response=resp_503)
    )

    resp_400 = httpx.Response(status_code=400, request=req)
    assert not _is_retryable_exception(
        httpx.HTTPStatusError("Bad request", request=req, response=resp_400)
    )

    resp_401 = httpx.Response(status_code=401, request=req)
    assert not _is_retryable_exception(
        httpx.HTTPStatusError("Unauthorized", request=req, response=resp_401)
    )

    assert _is_retryable_exception(httpx.ConnectTimeout("Connection timed out"))


@pytest.mark.asyncio
async def test_nim_retry_recovers_after_rate_limit() -> None:
    """NIMProvider retries on HTTP 429 and returns successful payload when recovered."""
    provider = NIMProvider(api_key="test-key")

    req = httpx.Request("POST", "https://integrate.api.nvidia.com/v1/embeddings")
    resp_429 = httpx.Response(status_code=429, request=req)
    resp_200 = httpx.Response(
        status_code=200,
        request=req,
        json={
            "data": [
                {"index": 0, "embedding": [0.5] * 2048},
            ]
        },
    )

    # First call 429, second call 200
    mock_post = AsyncMock(
        side_effect=[
            resp_429,
            resp_200,
        ]
    )

    with patch("httpx.AsyncClient.post", mock_post):
        embeddings = await provider.embed(["sample test text"])

        assert len(embeddings) == 1
        assert len(embeddings[0]) == 768
        assert mock_post.call_count == 2


@pytest.mark.asyncio
async def test_nim_retry_fails_after_persistent_rate_limit() -> None:
    """NIMProvider raises HTTPStatusError after exceeding retry attempts on persistent 429."""
    provider = NIMProvider(api_key="test-key")

    req = httpx.Request("POST", "https://integrate.api.nvidia.com/v1/embeddings")
    resp_429 = httpx.Response(status_code=429, request=req)

    mock_post = AsyncMock(return_value=resp_429)

    with patch("httpx.AsyncClient.post", mock_post), patch("asyncio.sleep", AsyncMock()):
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await provider.embed(["sample test text"])

        assert exc_info.value.response.status_code == 429
        # Max attempts configured is 5
        assert mock_post.call_count == 5
