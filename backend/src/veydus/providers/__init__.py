# ─────────────────────────────────────────────────────────────────
# VEYDUS — Inference Providers Package
# ─────────────────────────────────────────────────────────────────
# What:  Public interface for VEYDUS inference providers (embeddings & LLMs).
# How:   Exports InferenceProvider protocol, factory function, and implementations.
# Why:   HLD §7.2 requires swappable providers (Mock and NIM).
# Tools: Python typing, module exports.
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

from veydus.providers.base import InferenceProvider, get_inference_provider
from veydus.providers.mock import MockProvider
from veydus.providers.nim import NIMProvider, apply_matryoshka_truncation

__all__ = [
    "InferenceProvider",
    "MockProvider",
    "NIMProvider",
    "apply_matryoshka_truncation",
    "get_inference_provider",
]
