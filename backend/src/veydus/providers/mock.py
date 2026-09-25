# ─────────────────────────────────────────────────────────────────
# VEYDUS — Mock Inference Provider
# ─────────────────────────────────────────────────────────────────
# What:  Hermetic, offline inference provider for local testing and CI.
# How:   Generates deterministic, unit-normalized (L2) synthetic embedding
#        vectors using numpy and SHA-256 seeding.  Simulates streaming
#        and synchronous generation without any external network calls.
# Why:   Enables zero-dependency local test suites (unit, isolation, integration)
#        that run quickly and reliably without needing NVIDIA NIM credentials.
# Tools: numpy (deterministic vector math, L2 normalization), hashlib, asyncio.
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

import asyncio
import hashlib
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class MockProvider:
    """Hermetic mock inference provider for local testing and CI."""

    def __init__(self, dimensions: int = 768) -> None:
        self.dimensions = dimensions

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Generate deterministic, L2-normalized embeddings based on text hashes."""
        embeddings: list[list[float]] = []
        for text in texts:
            # Seed numpy with hash of input text for determinism
            text_hash = hashlib.sha256(text.encode("utf-8")).digest()
            seed = int.from_bytes(text_hash[:4], byteorder="big")
            rng = np.random.default_rng(seed)
            raw_vec = rng.standard_normal(self.dimensions).astype(np.float32)

            # L2 normalize
            norm = float(np.linalg.norm(raw_vec))
            normalized_vec = raw_vec / norm if norm > 0 else raw_vec

            embeddings.append(normalized_vec.tolist())

        return embeddings

    async def generate(self, prompt: str, system_prompt: str | None = None) -> str:
        """Return a deterministic mock response."""
        return f"[MOCK RESPONSE] Responding to: {prompt[:50]}..."

    async def generate_stream(
        self, prompt: str, system_prompt: str | None = None
    ) -> AsyncIterator[str]:
        """Stream simulated tokens with minimal async yield."""
        chunks = ["[MOCK STREAM] ", f"Echo: {prompt[:30]}", "... ", "Completed."]
        for chunk in chunks:
            await asyncio.sleep(0.01)
            yield chunk
