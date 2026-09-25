# ─────────────────────────────────────────────────────────────────
# VEYDUS — Chunks Repository [SECURITY-CRITICAL CHOKEPOINT]
# ─────────────────────────────────────────────────────────────────
# What:  Data access repository for chunks, source metadata, and vector search.
# How:   - Executes HLD §6.3 retrieval query verbatim with ANN vector ordering
#          and access predicates (org_id, department_id, hierarchy_level).
#        - Configures HNSW iterative scan parameters (iterative_scan, max_scan_tuples, ef_search).
#        - Atomic batch chunk insertion with copied access tags (HLD §7.2).
# Why:   HLD §16 mandate: Access control attributes (department_id, hierarchy_level)
#        are strictly confined to authz/policy.py and db/repositories/chunks.py.
#        Enforces single chokepoint for access-tagged chunk persistence and retrieval.
# Tools: SQLAlchemy (text, AsyncConnection), pgvector, Pydantic v2, uuid.UUID.
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import text

from veydus.authz.models import ChunkMeta

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncConnection

    from veydus.authz.models import RetrievalFilter, UserScope

logger = logging.getLogger(__name__)


class RetrievedChunk(BaseModel):
    """Structured chunk result returned from database vector retrieval."""

    id: UUID
    org_id: UUID
    document_id: UUID
    content: str
    page_number: int | None = None
    department_id: UUID
    hierarchy_level: int = Field(ge=1)
    document_title: str
    source_name: str
    similarity: float = Field(description="Cosine similarity: 1 - cosine_distance")
    relevance_score: float | None = Field(default=None, description="Cross-encoder relevance score")

    @property
    def meta(self) -> ChunkMeta:
        """Construct ChunkMeta for assertion testing and out-of-scope verification."""
        return ChunkMeta(
            chunk_id=self.id,
            org_id=self.org_id,
            department_id=self.department_id,
            hierarchy_level=self.hierarchy_level,
        )


async def get_source_access_info(
    conn: AsyncConnection, org_id: UUID, source_id: UUID
) -> tuple[UUID, int, str] | None:
    """Retrieve department_id, hierarchy_level, and status for a source."""
    res = await conn.execute(
        text("""
            SELECT department_id, hierarchy_level, status
            FROM sources
            WHERE id = :source_id AND org_id = :org_id
        """),
        {"source_id": source_id, "org_id": org_id},
    )
    row = res.fetchone()
    if row is None:
        return None
    return (row[0], row[1], row[2])


async def insert_chunks_batch(
    conn: AsyncConnection,
    org_id: UUID,
    document_id: UUID,
    source_id: UUID,
    department_id: UUID,
    hierarchy_level: int,
    chunks_data: list[dict[str, Any]],
) -> None:
    """Insert chunks copying access tags atomically [SECURITY-CRITICAL].

    CRITICAL INVARIANT (HLD §7.2): department_id and hierarchy_level
    MUST be copied onto each chunk row in the same transaction as insertion.
    """
    for item in chunks_data:
        await conn.execute(
            text("""
                INSERT INTO chunks (
                    id, org_id, document_id, source_id,
                    department_id, hierarchy_level, ordinal,
                    content, token_count, page_number, embedding
                ) VALUES (
                    :id, :org_id, :document_id, :source_id,
                    :department_id, :hierarchy_level, :ordinal,
                    :content, :token_count, :page_number, CAST(:embedding AS vector)
                )
            """),
            {
                "id": item["id"],
                "org_id": org_id,
                "document_id": document_id,
                "source_id": source_id,
                "department_id": department_id,
                "hierarchy_level": hierarchy_level,
                "ordinal": item["ordinal"],
                "content": item["content"],
                "token_count": item["token_count"],
                "page_number": item["page_number"],
                "embedding": item["embedding"],
            },
        )


async def count_chunks_for_source(conn: AsyncConnection, org_id: UUID, source_id: UUID) -> int:
    """Count number of chunks associated with a source."""
    res = await conn.execute(
        text("""
            SELECT count(*) FROM chunks
            WHERE source_id = :source_id AND org_id = :org_id
        """),
        {"source_id": source_id, "org_id": org_id},
    )
    count = res.scalar_one_or_none()
    return count or 0


async def retrieve_candidate_chunks(
    conn: AsyncConnection,
    scope: UserScope | RetrievalFilter,
    query_embedding: list[float],
    k: int = 30,
    ef_search: int = 100,
    max_scan_tuples: int = 20000,
) -> list[RetrievedChunk]:
    """Execute HLD §6.3 vector retrieval query with index scan pushdown.

    CRITICAL INVARIANT (HLD §6.3):
    1. The access predicate (org_id, department_id, hierarchy_level) is in
       the same statement as the ANN ordering (no post-filtering in app code).
    2. Parameters are bound from server-resolved RetrievalFilter only.
    3. Iterative index scan is enabled to prevent candidate exhaustion.
    """
    from veydus.authz.models import RetrievalFilter
    from veydus.authz.policy import build_retrieval_filter

    if isinstance(scope, RetrievalFilter):
        retrieval_filter = scope
    else:
        retrieval_filter = build_retrieval_filter(scope)
    # 1. Configure HNSW iterative scan parameters for this transaction
    try:
        await conn.execute(text("SELECT set_config('hnsw.iterative_scan', 'relaxed_order', true)"))
        await conn.execute(
            text("SELECT set_config('hnsw.max_scan_tuples', :max_scan, true)"),
            {"max_scan": str(max_scan_tuples)},
        )
        await conn.execute(
            text("SELECT set_config('hnsw.ef_search', :ef_search, true)"),
            {"ef_search": str(ef_search)},
        )
    except Exception as e:
        logger.debug("Failed to set HNSW iterative scan parameters: %s", e)

    # 2. Format embedding vector as literal string: '[0.1,0.2,...]'
    emb_str = f"[{','.join(str(v) for v in query_embedding)}]"

    # 3. Execute HLD §6.3 query verbatim
    query = text("""
        SELECT
            c.id,
            c.content,
            c.document_id,
            c.page_number,
            c.department_id,
            c.hierarchy_level,
            d.title            AS document_title,
            s.display_name     AS source_name,
            1 - (c.embedding <=> CAST(:query_embedding AS vector)) AS similarity
        FROM chunks c
        JOIN documents d ON d.id = c.document_id
        JOIN sources   s ON s.id = c.source_id
        WHERE c.org_id          = :org_id
          AND c.department_id   = :department_id
          AND c.hierarchy_level <= :hierarchy_level
        ORDER BY c.embedding <=> CAST(:query_embedding AS vector)
        LIMIT :k
    """)

    params = {
        "org_id": retrieval_filter.org_id,
        "department_id": retrieval_filter.department_id,
        "hierarchy_level": retrieval_filter.max_hierarchy_level,
        "query_embedding": emb_str,
        "k": k,
    }

    res = await conn.execute(query, params)
    rows = res.fetchall()

    chunks: list[RetrievedChunk] = []
    for row in rows:
        chunks.append(
            RetrievedChunk(
                id=row[0],
                org_id=retrieval_filter.org_id,
                content=row[1],
                document_id=row[2],
                page_number=row[3],
                department_id=row[4],
                hierarchy_level=row[5],
                document_title=row[6],
                source_name=row[7],
                similarity=float(row[8]),
            )
        )

    return chunks
