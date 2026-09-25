# ─────────────────────────────────────────────────────────────────
# VEYDUS — Inference Provider Interface & Factory
# ─────────────────────────────────────────────────────────────────
# What:  Defines the abstract protocol for inference providers (embeddings
#        and text generation) and a factory function to instantiate them.
# How:   Uses Python's typing.Protocol for structural subtyping.
#        The factory inspects Settings.inference_provider ("mock", "nim")
#        to dynamically return the appropriate concrete adapter.
# Why:   HLD §7.2 requires swappable inference providers so that local
#        development and CI can run hermetically without external API keys,
#        while production connects seamlessly to NVIDIA NIM endpoints.
# Tools: Python typing (Protocol, runtime_checkable), AsyncIterator.
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from veydus.config import Settings


@runtime_checkable
class InferenceProvider(Protocol):
    """Protocol for embedding and generation models."""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Generate dense vector embeddings for a list of text strings.

        Returns:
            A list of 768-dimensional L2-normalized float vectors.
        """
        ...

    async def generate(self, prompt: str, system_prompt: str | None = None) -> str:
        """Generate a complete text response for a given prompt."""
        ...

    def generate_stream(self, prompt: str, system_prompt: str | None = None) -> AsyncIterator[str]:
        """Stream generated text tokens for a given prompt."""
        ...


def get_inference_provider(settings: Settings | None = None) -> InferenceProvider:
    """Factory to retrieve the configured inference provider instance."""
    if settings is None:
        from veydus.config import settings as default_settings

        settings = default_settings

    provider_name = settings.inference_provider.lower().strip()

    if provider_name == "mock":
        from veydus.providers.mock import MockProvider

        return MockProvider(
            dimensions=settings.embedding_dimensions,
        )
    elif provider_name == "nim":
        from veydus.providers.nim import NIMProvider

        return NIMProvider(
            api_key=settings.nvidia_api_key,
            base_url=settings.nim_base_url,
            embedding_model=settings.embedding_model,
            dimensions=settings.embedding_dimensions,
            generation_model=settings.generation_model,
        )
    else:
        raise ValueError(
            f"Unsupported inference provider '{provider_name}'. Must be 'mock' or 'nim'."
        )
