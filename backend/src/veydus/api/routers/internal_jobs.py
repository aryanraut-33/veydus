# ─────────────────────────────────────────────────────────────────
# VEYDUS — Internal Worker Jobs API Router
# ─────────────────────────────────────────────────────────────────
# What:  Internal HTTP endpoints for triggering document ingestion
#        and verifiable deletion jobs.
# How:   Uses FastAPI APIRouter, Pydantic v2 schemas for request/response
#        validation, base64 payload decoding for file content, and delegates
#        execution to IngestionPipeline.
# Why:   HLD §7.2 & §7.5 specify asynchronous worker job execution for heavy
#        ingestion and deletion operations.
# Tools: FastAPI (APIRouter, Depends, HTTPException), Pydantic v2, base64, uuid.
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

import base64
import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from veydus.ingestion.pipeline import (
    DeletionResult,
    IngestionPipeline,
    IngestionResult,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal/jobs", tags=["internal-jobs"])


class IngestJobRequest(BaseModel):
    """Payload to trigger an ingestion job for an uploaded document."""

    org_id: UUID
    source_id: UUID
    filename: str
    file_content_base64: str = Field(description="Base64-encoded raw file content")
    mime_type: str | None = None
    department_id: UUID | None = None
    hierarchy_level: int = 1
    actor_user_id: UUID | None = None


class DeleteJobRequest(BaseModel):
    """Payload to trigger verifiable hard deletion of a source."""

    org_id: UUID
    source_id: UUID
    actor_user_id: UUID | None = None


def get_pipeline() -> IngestionPipeline:
    """Dependency provider for IngestionPipeline instance."""
    return IngestionPipeline()


@router.post("/ingest", response_model=IngestionResult, status_code=status.HTTP_200_OK)
async def trigger_ingest_job(request: IngestJobRequest) -> Any:
    """Trigger document parsing, chunking, embedding, and indexing."""
    try:
        content_bytes = base64.b64decode(request.file_content_base64)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid base64 payload: {e}",
        ) from e

    pipeline = get_pipeline()
    result = await pipeline.ingest_document(
        org_id=request.org_id,
        source_id=request.source_id,
        filename=request.filename,
        content=content_bytes,
        mime_type=request.mime_type,
        department_id=request.department_id,
        hierarchy_level=request.hierarchy_level,
        actor_user_id=request.actor_user_id,
    )

    if result.status == "failed":
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ingestion failed: {result.error_detail}",
        )

    return result


@router.post("/delete", response_model=DeletionResult, status_code=status.HTTP_200_OK)
async def trigger_delete_job(request: DeleteJobRequest) -> Any:
    """Trigger verifiable hard deletion of a knowledge source."""
    pipeline = get_pipeline()
    try:
        result = await pipeline.delete_source(
            org_id=request.org_id,
            source_id=request.source_id,
            actor_user_id=request.actor_user_id,
        )
        return result
    except Exception as e:
        logger.exception("Failed to delete source %s: %s", request.source_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Deletion failed: {e}",
        ) from e
