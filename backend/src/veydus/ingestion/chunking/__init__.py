# ─────────────────────────────────────────────────────────────────
# VEYDUS — Document Chunking Package
# ─────────────────────────────────────────────────────────────────
# What:  Public exports for chunking strategies and models.
# How:   Exports ChunkItem, ChunkingStrategy, and RecursiveStructureChunker.
# Why:   HLD §7.2 requires modular, structure-aware document chunking.
# Tools: Python module exports.
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

from veydus.ingestion.chunking.base import ChunkingStrategy, ChunkItem
from veydus.ingestion.chunking.recursive import RecursiveStructureChunker

__all__ = [
    "ChunkItem",
    "ChunkingStrategy",
    "RecursiveStructureChunker",
]
