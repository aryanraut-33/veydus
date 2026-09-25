# ─────────────────────────────────────────────────────────────────
# VEYDUS — Integration Tests: Document Ingestion Pipeline
# ─────────────────────────────────────────────────────────────────
# What:  Validates end-to-end ingestion of documents into PostgreSQL + pgvector,
#        including access tagging invariants, idempotency enforcement,
#        transient staging cleanup, and audit logging.
# How:   Executes IngestionPipeline against a migrated testcontainer database,
#        verifies chunk rows and denormalized department/level metadata, asserts
#        zero remaining files in staging directory, and tests idempotent re-runs.
# Why:   HLD §7.2, §7.5, and ADR-0004 require zero-leak access tagging on chunks,
#        idempotent content-hash de-duplication, and transient staging guarantees.
# Tools: pytest, psycopg2, SQLAlchemy (asyncpg), pgvector, Pydantic v2.
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest

from veydus.config import settings
from veydus.ingestion.pipeline import IngestionPipeline
from veydus.ingestion.staging import LocalStorage
from veydus.providers.mock import MockProvider


def _seed_test_source(migrate_conn: Any) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    """Helper to seed org, dept, user, and source as veydus_migrate."""
    with migrate_conn.cursor() as cur:
        # Org
        cur.execute(
            "INSERT INTO organizations (name, slug) VALUES ('Ingest Test Org', %s) RETURNING id",
            (f"ingest-org-{uuid.uuid4().hex[:8]}",),
        )
        org_id = cur.fetchone()[0]

        # Dept
        cur.execute(
            "INSERT INTO departments (org_id, name, max_level) VALUES (%s, 'Engineering', 4) RETURNING id",
            (org_id,),
        )
        dept_id = cur.fetchone()[0]

        # User
        cur.execute(
            """INSERT INTO users (org_id, idp_subject, email, display_name, role, status)
               VALUES (%s, %s, %s, 'Ingest Tester', 'user', 'active') RETURNING id""",
            (org_id, f"sub-{uuid.uuid4().hex[:8]}", f"tester-{uuid.uuid4().hex[:8]}@example.com"),
        )
        user_id = cur.fetchone()[0]

        # Source
        cur.execute(
            """INSERT INTO sources (org_id, kind, display_name, department_id, hierarchy_level, created_by)
               VALUES (%s, 'upload', 'Test Spec', %s, 2, %s) RETURNING id""",
            (org_id, dept_id, user_id),
        )
        source_id = cur.fetchone()[0]

    migrate_conn.commit()
    return org_id, dept_id, user_id, source_id


@pytest.mark.integration
@pytest.mark.asyncio
async def test_markdown_ingestion_pipeline_and_access_tagging(
    migrated_db: dict[str, Any],
    setup_test_engine: str,
    migrate_conn: Any,
    tmp_path: Path,
) -> None:
    """Ingest markdown document, verify access tags on chunks, transient cleanup, and audit log."""
    org_id, dept_id, user_id, source_id = _seed_test_source(migrate_conn)

    staging = LocalStorage(base_dir=tmp_path / "staging")
    pipeline = IngestionPipeline(
        inference_provider=MockProvider(dimensions=768),
        staging_storage=staging,
        settings=settings,
    )

    md_content = b"""# Safety Guidelines 2026

## Protocol One
Ensure emergency stops are tested daily before operation.

| Equipment | Inspection Schedule |
| --- | --- |
| Turbine A | Daily |
| Generator B | Weekly |

## Protocol Two
Follow strict lockout-tagout procedures during maintenance.
"""

    result = await pipeline.ingest_document(
        org_id=org_id,
        source_id=source_id,
        filename="safety_guidelines.md",
        content=md_content,
        mime_type="text/markdown",
        department_id=dept_id,
        hierarchy_level=2,
        actor_user_id=user_id,
    )

    assert result.status == "completed"
    assert not result.is_duplicate
    assert result.chunks_inserted >= 2
    assert result.document_id is not None

    # Verify Database Invariants (via migrate_conn)
    with migrate_conn.cursor() as cur:
        # 1. Source status is ready
        cur.execute("SELECT status, error_detail FROM sources WHERE id = %s", (str(source_id),))
        src_row = cur.fetchone()
        assert src_row[0] == "ready"
        assert src_row[1] is None

        # 2. Document record exists with correct content hash
        cur.execute(
            "SELECT id, title, content_hash FROM documents WHERE id = %s",
            (str(result.document_id),),
        )
        doc_row = cur.fetchone()
        assert doc_row is not None
        assert doc_row[1] == "Safety Guidelines 2026"

        # 3. CRITICAL ACCESS TAGGING INVARIANT: all chunks have exact department_id and hierarchy_level
        cur.execute(
            """SELECT department_id, hierarchy_level, ordinal, token_count, page_number
               FROM chunks WHERE document_id = %s ORDER BY ordinal ASC""",
            (str(result.document_id),),
        )
        chunks = cur.fetchall()
        assert len(chunks) == result.chunks_inserted
        for chk in chunks:
            assert chk[0] == dept_id  # department_id must match
            assert chk[1] == 2  # hierarchy_level must match
            assert chk[3] > 0  # token_count > 0

        # 4. Audit Log contains document_ingested event
        cur.execute(
            "SELECT event_type, payload FROM audit_log WHERE org_id = %s AND event_type = 'document_ingested'",
            (str(org_id),),
        )
        audit_rows = cur.fetchall()
        assert len(audit_rows) >= 1
        payload = audit_rows[0][1]
        if isinstance(payload, str):
            payload = json.loads(payload)
        assert payload["source_id"] == str(source_id)
        assert payload["chunks_count"] == result.chunks_inserted

    # 5. TRANSIENT STAGING CLEANUP INVARIANT: staging directory must be clean
    staged_source_dir = Path(staging.base_dir) / str(source_id)
    assert not staged_source_dir.exists() or not any(staged_source_dir.iterdir())


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ingestion_idempotency_prevents_duplicate_chunks(
    migrated_db: dict[str, Any],
    setup_test_engine: str,
    migrate_conn: Any,
    tmp_path: Path,
) -> None:
    """Re-ingesting the identical document is an idempotent no-op."""
    org_id, dept_id, user_id, source_id = _seed_test_source(migrate_conn)

    staging = LocalStorage(base_dir=tmp_path / "staging")
    pipeline = IngestionPipeline(
        inference_provider=MockProvider(dimensions=768),
        staging_storage=staging,
        settings=settings,
    )

    doc_bytes = b"Standard Operating Procedure for Server Maintenance."

    # First ingestion
    first_res = await pipeline.ingest_document(
        org_id=org_id,
        source_id=source_id,
        filename="sop.txt",
        content=doc_bytes,
        department_id=dept_id,
        hierarchy_level=2,
    )
    assert first_res.status == "completed"
    assert not first_res.is_duplicate
    assert first_res.chunks_inserted >= 1

    # Record total chunks in DB
    with migrate_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM chunks WHERE source_id = %s", (str(source_id),))
        count_after_first = cur.fetchone()[0]

    # Second ingestion with same content
    second_res = await pipeline.ingest_document(
        org_id=org_id,
        source_id=source_id,
        filename="sop.txt",
        content=doc_bytes,
        department_id=dept_id,
        hierarchy_level=2,
    )
    assert second_res.status == "completed"
    assert second_res.is_duplicate
    assert second_res.chunks_inserted == 0

    # Assert no duplicate chunks inserted
    with migrate_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM chunks WHERE source_id = %s", (str(source_id),))
        count_after_second = cur.fetchone()[0]

    assert count_after_second == count_after_first
