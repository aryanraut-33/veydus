# ─────────────────────────────────────────────────────────────────
# VEYDUS — Unit Tests: Internal Worker Jobs API Router
# ─────────────────────────────────────────────────────────────────
# What:  Validates FastAPI endpoints for internal worker jobs
#        (/internal/jobs/ingest, /internal/jobs/delete, and /health).
# How:   Uses httpx.AsyncClient with ASGITransport to issue HTTP requests
#        against the FastAPI application, mocking IngestionPipeline methods.
# Why:   HLD §7.2 requires robust HTTP endpoints for worker orchestration
#        with base64 payload decoding, validation, and error reporting.
# Tools: pytest, httpx (AsyncClient, ASGITransport), unittest.mock, uuid, base64.
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

import base64
import uuid
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from veydus.api.main import app
from veydus.ingestion.pipeline import DeletionResult, IngestionResult


@pytest.mark.asyncio
async def test_healthcheck_endpoint() -> None:
    """GET /health returns 200 OK and service status."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["service"] == "veydus-api"


@pytest.mark.asyncio
async def test_ingest_job_endpoint_success() -> None:
    """POST /internal/jobs/ingest decodes base64 content and invokes IngestionPipeline."""
    org_id = uuid.uuid4()
    source_id = uuid.uuid4()
    doc_id = uuid.uuid4()
    raw_content = b"Technical documentation content."
    b64_content = base64.b64encode(raw_content).decode("utf-8")

    mock_result = IngestionResult(
        source_id=source_id,
        document_id=doc_id,
        content_hash="abc123hash",
        chunks_inserted=3,
        status="completed",
        is_duplicate=False,
    )

    with patch(
        "veydus.api.routers.internal_jobs.IngestionPipeline.ingest_document",
        new_callable=AsyncMock,
        return_value=mock_result,
    ) as mock_ingest:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            payload = {
                "org_id": str(org_id),
                "source_id": str(source_id),
                "filename": "tech_spec.txt",
                "file_content_base64": b64_content,
                "mime_type": "text/plain",
                "hierarchy_level": 1,
            }
            response = await client.post("/internal/jobs/ingest", json=payload)

            assert response.status_code == 200
            data = response.json()
            assert data["source_id"] == str(source_id)
            assert data["document_id"] == str(doc_id)
            assert data["chunks_inserted"] == 3
            assert data["status"] == "completed"

            mock_ingest.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_job_endpoint_success() -> None:
    """POST /internal/jobs/delete invokes verifiable hard deletion."""
    org_id = uuid.uuid4()
    source_id = uuid.uuid4()

    mock_result = DeletionResult(
        source_id=source_id,
        deleted_chunks=5,
        verified=True,
        audit_log_id=101,
    )

    with patch(
        "veydus.api.routers.internal_jobs.IngestionPipeline.delete_source",
        new_callable=AsyncMock,
        return_value=mock_result,
    ) as mock_delete:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            payload = {
                "org_id": str(org_id),
                "source_id": str(source_id),
            }
            response = await client.post("/internal/jobs/delete", json=payload)

            assert response.status_code == 200
            data = response.json()
            assert data["source_id"] == str(source_id)
            assert data["deleted_chunks"] == 5
            assert data["verified"] is True
            assert data["audit_log_id"] == 101

            mock_delete.assert_awaited_once()
