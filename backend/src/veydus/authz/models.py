# ─────────────────────────────────────────────────────────────────
# VEYDUS — Authorization Domain Models
# ─────────────────────────────────────────────────────────────────
# What:  Data models representing identity scope (UserScope), compiled
#        access predicates (RetrievalFilter), and chunk access metadata (ChunkMeta).
# How:   Uses Pydantic v2 BaseModel for strict validation and immutability.
# Why:   HLD §2.3 and §6.3 mandate that access predicates are strictly typed
#        and derived solely from server-side resolved scope, decoupling the
#        RAG pipeline from ad-hoc authorization queries and paving the path
#        for seamless ABAC-to-ReBAC migration (ADR-0003).
# Tools: Pydantic v2 (BaseModel, Field), uuid.UUID.
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


class UserScope(BaseModel):
    """Server-resolved identity and access scope for an authenticated request."""

    org_id: UUID = Field(description="Organization tenant identifier")
    user_id: UUID = Field(description="User identifier")
    department_id: UUID = Field(description="Assigned department identifier")
    hierarchy_level: int = Field(ge=1, description="Clearance level within department (1 = lowest)")
    role: str = Field(default="user", description="User role: 'user' or 'operator'")
    scope_version: int = Field(
        default=1, description="Scope version for cache and session invalidation"
    )


class RetrievalFilter(BaseModel):
    """Compiled access constraints passed to database vector search."""

    org_id: UUID
    department_id: UUID
    max_hierarchy_level: int = Field(ge=1, description="Upper bound on chunk hierarchy_level")


class ChunkMeta(BaseModel):
    """Access control metadata associated with an indexed or retrieved chunk."""

    chunk_id: UUID
    org_id: UUID
    department_id: UUID
    hierarchy_level: int = Field(ge=1)
