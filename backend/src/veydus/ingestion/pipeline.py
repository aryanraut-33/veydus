# ─────────────────────────────────────────────────────────────────
# VEYDUS — Document Ingestion & Deletion Pipeline
# ─────────────────────────────────────────────────────────────────
# What:  End-to-end pipeline orchestrating document staging, parsing,
#        chunking, Matryoshka embedding, atomic access-tagged persistence,
#        transient cleanup, idempotency enforcement, and verifiable hard deletion.
# How:   - SHA-256 content hash check for idempotency (skips duplicates).
#        - Transient staging via StagingStorage context manager.
#        - Multi-format parsing via Docling / Text parsers.
#        - Structure-aware chunking via RecursiveStructureChunker.
#        - Batched embedding generation via InferenceProvider.
#        - Single-transaction insertion via tenant_transaction(org_id)
#          strictly copying department_id and hierarchy_level onto every chunk row.
#        - Immediate filesystem cleanup post-ingestion.
#        - Verifiable cascading hard deletion asserting count(*) == 0 and logging audit event.
# Why:   HLD §7.2, §7.5, and ADR-0004 specify zero-leak access tagging,
#        idempotent ingestion, transient staging, and verifiable deletion guarantees.
# Tools: hashlib, uuid, json, SQLAlchemy (text), asyncio, Pydantic v2.
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field
from sqlalchemy import text

from veydus.config import Settings
from veydus.config import settings as default_settings
from veydus.db.session import tenant_transaction
from veydus.ingestion.chunking.recursive import RecursiveStructureChunker
from veydus.ingestion.parsers.base import get_parser_for_file
from veydus.ingestion.staging import StagingStorage, get_staging_storage
from veydus.providers.base import InferenceProvider, get_inference_provider

if TYPE_CHECKING:
    from uuid import UUID

    from veydus.ingestion.chunking.base import ChunkingStrategy

logger = logging.getLogger(__name__)


class IngestionResult(BaseModel):
    """Result summary of a document ingestion job."""

    source_id: uuid.UUID
    document_id: uuid.UUID | None = None
    content_hash: str
    chunks_inserted: int = 0
    status: str = Field(description="'completed', 'duplicate', or 'failed'")
    is_duplicate: bool = False
    error_detail: str | None = None


class DeletionResult(BaseModel):
    """Result summary of a verifiable hard deletion operation."""

    source_id: uuid.UUID
    deleted_chunks: int = 0
    verified: bool = False
    audit_log_id: int | None = None


class IngestionPipeline:
    """Orchestrator for document ingestion and deletion."""

    def __init__(
        self,
        inference_provider: InferenceProvider | None = None,
        staging_storage: StagingStorage | None = None,
        chunker: ChunkingStrategy | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or default_settings
        self.inference_provider = inference_provider or get_inference_provider(self.settings)
        self.staging_storage = staging_storage or get_staging_storage(self.settings)
        self.chunker = chunker or RecursiveStructureChunker(settings=self.settings)

    async def ingest_document(
        self,
        org_id: UUID,
        source_id: UUID,
        filename: str,
        content: bytes,
        mime_type: str | None = None,
        department_id: UUID | None = None,
        hierarchy_level: int = 1,
        actor_user_id: UUID | None = None,
    ) -> IngestionResult:
        """Execute full ingestion pipeline for an uploaded document."""
        # 1. Compute SHA-256 content hash
        content_hash = hashlib.sha256(content).hexdigest()
        detected_mime = mime_type or "application/octet-stream"

        try:
            # 2. Idempotency Check & Source Metadata Lookup
            async with tenant_transaction(org_id) as conn:
                # Query existing document with identical content_hash under this source
                existing_doc = await conn.execute(
                    text("""
                        SELECT id FROM documents
                        WHERE org_id = :org_id
                          AND source_id = :source_id
                          AND content_hash = :content_hash
                        LIMIT 1
                    """),
                    {
                        "org_id": org_id,
                        "source_id": source_id,
                        "content_hash": content_hash,
                    },
                )
                existing_row = existing_doc.fetchone()
                if existing_row:
                    doc_id = existing_row[0]
                    # Check if chunks already exist
                    chunk_check = await conn.execute(
                        text("""
                            SELECT count(*) FROM chunks
                            WHERE org_id = :org_id AND document_id = :document_id
                        """),
                        {"org_id": org_id, "document_id": doc_id},
                    )
                    existing_chunks_count = chunk_check.scalar_one_or_none() or 0
                    if existing_chunks_count > 0:
                        logger.info(
                            "Idempotency hit: document %s with hash %s already ingested (%d chunks)",
                            doc_id,
                            content_hash,
                            existing_chunks_count,
                        )
                        return IngestionResult(
                            source_id=source_id,
                            document_id=doc_id,
                            content_hash=content_hash,
                            chunks_inserted=0,
                            status="completed",
                            is_duplicate=True,
                        )

                # Fetch department_id and hierarchy_level from sources via repository chokepoint
                from veydus.db.repositories.chunks import get_source_access_info

                src_info = await get_source_access_info(conn, org_id, source_id)
                if src_info:
                    src_dept_id, src_level, _ = src_info
                    if department_id is None:
                        department_id = src_dept_id
                    hierarchy_level = src_level

                # Update source status to processing
                await conn.execute(
                    text("""
                        UPDATE sources
                        SET status = 'processing', error_detail = NULL
                        WHERE id = :source_id AND org_id = :org_id
                    """),
                    {"source_id": source_id, "org_id": org_id},
                )

            if department_id is None:
                raise ValueError(f"Source {source_id} does not have an assigned department_id.")

            # 3. Transient Staging, Parsing, Chunking & Embedding
            chunks_to_insert: list[dict[str, Any]] = []
            doc_title = filename

            async with self.staging_storage.stage_context(
                source_id, filename, content
            ) as staged_path:
                parser = get_parser_for_file(staged_path)
                parsed_doc = await parser.parse(staged_path, mime_type=detected_mime)
                doc_title = parsed_doc.title or filename

                # Chunk document
                chunk_items = self.chunker.chunk(parsed_doc)
                if not chunk_items:
                    logger.warning("Document %s yielded 0 chunks", filename)

                # Batch embed chunks
                batch_size = self.settings.embedding_batch_size
                all_embeddings: list[list[float]] = []
                for i in range(0, len(chunk_items), batch_size):
                    batch = chunk_items[i : i + batch_size]
                    batch_texts = [item.content for item in batch]
                    batch_vectors = await self.inference_provider.embed(batch_texts)
                    all_embeddings.extend(batch_vectors)

                # Prepare chunk records
                for item, emb in zip(chunk_items, all_embeddings, strict=True):
                    # Format pgvector string: '[0.1,0.2,...]'
                    emb_str = f"[{','.join(str(v) for v in emb)}]"
                    # Approximate token count: 1 token ≈ 4 characters
                    token_count = max(1, len(item.content) // 4)

                    chunks_to_insert.append(
                        {
                            "id": uuid.uuid4(),
                            "ordinal": item.chunk_index,
                            "content": item.content,
                            "token_count": token_count,
                            "page_number": item.page_number,
                            "embedding": emb_str,
                        }
                    )

            # 4. Atomic Transaction Persistence [SECURITY-CRITICAL]
            # Chunks, access metadata, and document inserted in the exact same transaction
            new_doc_id = uuid.uuid4()
            async with tenant_transaction(org_id) as conn:
                # Insert document
                await conn.execute(
                    text("""
                        INSERT INTO documents (
                            id, org_id, source_id, external_ref, title,
                            mime_type, page_count, content_hash
                        ) VALUES (
                            :id, :org_id, :source_id, :external_ref, :title,
                            :mime_type, :page_count, :content_hash
                        )
                    """),
                    {
                        "id": new_doc_id,
                        "org_id": org_id,
                        "source_id": source_id,
                        "external_ref": filename,
                        "title": doc_title,
                        "mime_type": detected_mime,
                        "page_count": getattr(parsed_doc, "page_count", 1)
                        if "parsed_doc" in locals()
                        else 1,
                        "content_hash": content_hash,
                    },
                )

                # Insert chunks with denormalized access metadata copied from source via repository chokepoint
                from veydus.db.repositories.chunks import insert_chunks_batch

                await insert_chunks_batch(
                    conn=conn,
                    org_id=org_id,
                    document_id=new_doc_id,
                    source_id=source_id,
                    department_id=department_id,
                    hierarchy_level=hierarchy_level,
                    chunks_data=chunks_to_insert,
                )

                # Update source status to ready
                await conn.execute(
                    text("""
                        UPDATE sources
                        SET status = 'ready', error_detail = NULL
                        WHERE id = :source_id AND org_id = :org_id
                    """),
                    {"source_id": source_id, "org_id": org_id},
                )

                # Record audit log entry
                audit_payload = {
                    "source_id": str(source_id),
                    "document_id": str(new_doc_id),
                    "filename": filename,
                    "content_hash": content_hash,
                    "chunks_count": len(chunks_to_insert),
                    "department_id": str(department_id),
                    "hierarchy_level": hierarchy_level,
                }
                await conn.execute(
                    text("""
                        INSERT INTO audit_log (
                            org_id, actor_user_id, event_type, payload
                        ) VALUES (
                            :org_id, :actor_user_id, 'document_ingested', CAST(:payload AS jsonb)
                        )
                    """),
                    {
                        "org_id": org_id,
                        "actor_user_id": actor_user_id,
                        "payload": json.dumps(audit_payload),
                    },
                )

            logger.info(
                "Successfully ingested document %s (%s) with %d chunks",
                new_doc_id,
                filename,
                len(chunks_to_insert),
            )
            return IngestionResult(
                source_id=source_id,
                document_id=new_doc_id,
                content_hash=content_hash,
                chunks_inserted=len(chunks_to_insert),
                status="completed",
                is_duplicate=False,
            )

        except Exception as exc:
            logger.exception("Ingestion failed for source %s (%s): %s", source_id, filename, exc)
            # Record failure status on source record
            try:
                async with tenant_transaction(org_id) as conn:
                    await conn.execute(
                        text("""
                            UPDATE sources
                            SET status = 'failed', error_detail = :error
                            WHERE id = :source_id AND org_id = :org_id
                        """),
                        {"source_id": source_id, "org_id": org_id, "error": str(exc)},
                    )
            except Exception as e:
                logger.error("Failed to record failure status on source %s: %s", source_id, e)

            return IngestionResult(
                source_id=source_id,
                document_id=None,
                content_hash=content_hash,
                chunks_inserted=0,
                status="failed",
                is_duplicate=False,
                error_detail=str(exc),
            )

    async def delete_source(
        self,
        org_id: UUID,
        source_id: UUID,
        actor_user_id: UUID | None = None,
    ) -> DeletionResult:
        """Verifiably delete a source and all associated documents/chunks (HLD §7.5)."""
        async with tenant_transaction(org_id) as conn:
            # 1. Count existing chunks prior to deletion
            count_res = await conn.execute(
                text("""
                    SELECT count(*) FROM chunks
                    WHERE source_id = :source_id AND org_id = :org_id
                """),
                {"source_id": source_id, "org_id": org_id},
            )
            initial_count = count_res.scalar_one_or_none() or 0

            # 2. Hard delete source (foreign key CASCADE removes documents and chunks)
            await conn.execute(
                text("""
                    DELETE FROM sources
                    WHERE id = :source_id AND org_id = :org_id
                """),
                {"source_id": source_id, "org_id": org_id},
            )

            # 3. Post-deletion assertion [SECURITY-CRITICAL]
            verify_res = await conn.execute(
                text("""
                    SELECT count(*) FROM chunks
                    WHERE source_id = :source_id AND org_id = :org_id
                """),
                {"source_id": source_id, "org_id": org_id},
            )
            remaining_chunks = verify_res.scalar_one_or_none() or 0
            if remaining_chunks != 0:
                raise RuntimeError(
                    f"Deletion verification failed: {remaining_chunks} chunks remain for source {source_id}"
                )

            # 4. Insert deletion_verified event into audit_log
            audit_payload = {
                "source_id": str(source_id),
                "deleted_chunks": initial_count,
                "remaining_chunks": 0,
            }
            audit_res = await conn.execute(
                text("""
                    INSERT INTO audit_log (
                        org_id, actor_user_id, event_type, payload
                    ) VALUES (
                        :org_id, :actor_user_id, 'deletion_verified', CAST(:payload AS jsonb)
                    ) RETURNING id
                """),
                {
                    "org_id": org_id,
                    "actor_user_id": actor_user_id,
                    "payload": json.dumps(audit_payload),
                },
            )
            audit_id_row = audit_res.fetchone()
            audit_id = audit_id_row[0] if audit_id_row else None

        logger.info(
            "Verifiable deletion completed for source %s (%d chunks purged)",
            source_id,
            initial_count,
        )
        return DeletionResult(
            source_id=source_id,
            deleted_chunks=initial_count,
            verified=True,
            audit_log_id=audit_id,
        )
