# ─────────────────────────────────────────────────────────────────
# VEYDUS — Authorization Package
# ─────────────────────────────────────────────────────────────────
# What:  Public interface for the VEYDUS authorization subsystem.
# How:   Exports UserScope, RetrievalFilter, ChunkMeta, and policy functions.
# Why:   HLD §2.3 chokepoint for access control models and predicate construction.
# Tools: Python module exports.
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

from veydus.authz.models import ChunkMeta, RetrievalFilter, UserScope
from veydus.authz.policy import build_retrieval_filter, can_access_chunk

__all__ = [
    "ChunkMeta",
    "RetrievalFilter",
    "UserScope",
    "build_retrieval_filter",
    "can_access_chunk",
]
