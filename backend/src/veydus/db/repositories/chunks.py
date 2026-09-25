# ─────────────────────────────────────────────────────────────────
# VEYDUS — Chunks Repository  [SECURITY-CRITICAL CHOKEPOINT]
# ─────────────────────────────────────────────────────────────────
# What:  Data access repository for chunks and source access metadata.
# How:   Executes SQL operations for inserting chunks with access tags
#        (department_id, hierarchy_level) and querying chunk counts/metadata.
# Why:   HLD §16 mandate: Access control attributes (department_id, hierarchy_level)
#        are strictly confined to authz/policy.py and db/repositories/chunks.py.
#        Enforces single chokepoint for access-tagged chunk persistence.
# Tools: SQLAlchemy (text, AsyncConnection), uuid, typing.
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import text

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.ext.asyncio import AsyncConnection


async def get_source_access_info(
    conn: AsyncConnection, org_id: uuid.UUID, source_id: uuid.UUID
) -> tuple[uuid.UUID, int, str] | None:
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
    org_id: uuid.UUID,
    document_id: uuid.UUID,
    source_id: uuid.UUID,
    department_id: uuid.UUID,
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


async def count_chunks_for_source(
    conn: AsyncConnection, org_id: uuid.UUID, source_id: uuid.UUID
) -> int:
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
