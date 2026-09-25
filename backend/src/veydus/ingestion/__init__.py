# ─────────────────────────────────────────────────────────────────
# VEYDUS — Document Ingestion Package
# ─────────────────────────────────────────────────────────────────
# What:  Public interface for the VEYDUS document ingestion subsystem.
# How:   Exports pipeline orchestrator, results models, and staging utilities.
# Why:   HLD §7.2 requires modular access to document ingestion and deletion.
# Tools: Python module exports.
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

from veydus.ingestion.pipeline import DeletionResult, IngestionPipeline, IngestionResult
from veydus.ingestion.staging import LocalStorage, StagingStorage, get_staging_storage

__all__ = [
    "DeletionResult",
    "IngestionPipeline",
    "IngestionResult",
    "LocalStorage",
    "StagingStorage",
    "get_staging_storage",
]
