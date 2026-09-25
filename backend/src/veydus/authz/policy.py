# ─────────────────────────────────────────────────────────────────
# VEYDUS — Authorization Policy Engine [SECURITY-CRITICAL CHOKEPOINT]
# ─────────────────────────────────────────────────────────────────
# What:  Single authoritative module for generating database retrieval
#        access filters and evaluating chunk-level access assertions.
# How:   - build_retrieval_filter(): Constructs structured RetrievalFilter
#          compiled directly into the pgvector retrieval SQL query.
#        - can_access_chunk(): Pure boolean evaluator used strictly as an
#          assertion detector in tests and the post-rerank safety gate.
# Why:   HLD §2.3 and §16 mandate that NO OTHER MODULE in the codebase is
#        permitted to construct access predicates. This structural chokepoint
#        guarantees that future ABAC-to-ReBAC migrations (ADR-0003) remain
#        confined to this single file without query refactoring across the codebase.
# Tools: Python typing, veydus.authz.models (UserScope, RetrievalFilter, ChunkMeta).
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

from typing import TYPE_CHECKING

from veydus.authz.models import RetrievalFilter

if TYPE_CHECKING:
    from veydus.authz.models import ChunkMeta, UserScope


def build_retrieval_filter(scope: UserScope) -> RetrievalFilter:
    """Construct structured access constraints compiled into the retrieval SQL query.

    CRITICAL INVARIANT (HLD §2.3, §6.3):
    This is the ONLY function permitted to build an access predicate for retrieval.
    The parameters are bound from server-side scope and never from user input.
    """
    return RetrievalFilter(
        org_id=scope.org_id,
        department_id=scope.department_id,
        max_hierarchy_level=scope.hierarchy_level,
    )


def can_access_chunk(scope: UserScope, chunk_meta: ChunkMeta) -> bool:
    """Evaluate whether a chunk's metadata satisfies the given user scope.

    CRITICAL SECURITY NOTICE (HLD §2.3, §8.6):
    This function is ASSERTION-ONLY. It is used by the adversarial isolation
    test suite and the post-rerank out-of-scope assertion detector.
    It must NEVER be used as a runtime filter to prune results (filtering at runtime
    would convert a critical security defect into a silent failure).
    """
    if chunk_meta.org_id != scope.org_id:
        return False
    if chunk_meta.department_id != scope.department_id:
        return False
    return chunk_meta.hierarchy_level <= scope.hierarchy_level
