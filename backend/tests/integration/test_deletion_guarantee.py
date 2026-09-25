# ─────────────────────────────────────────────────────────────────
# VEYDUS — Integration Tests: Verifiable Hard Deletion Guarantee
# ─────────────────────────────────────────────────────────────────
# What:  Validates verifiable hard deletion of sources, cascading removal
#        of documents and chunks, post-deletion zero-count assertion,
#        and audit log event generation.
# How:   Seeds knowledge source with documents and chunks, executes
#        IngestionPipeline.delete_source within a tenant transaction,
#        asserts that remaining chunks are zero, and verifies audit_log entry.
# Why:   HLD §7.5 requires hard deletion to be provably complete with
#        no orphaned chunks and an immutable audit trail entry.
# Tools: pytest, psycopg2, SQLAlchemy (asyncpg), Pydantic v2.
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from veydus.config import settings
from veydus.ingestion.pipeline import IngestionPipeline
from veydus.providers.mock import MockProvider


def _seed_populated_source(migrate_conn: Any) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Seed org, dept, user, source, document, and 3 chunks."""
    with migrate_conn.cursor() as cur:
        # Org
        cur.execute(
            "INSERT INTO organizations (name, slug) VALUES ('Delete Test Org', %s) RETURNING id",
            (f"del-org-{uuid.uuid4().hex[:8]}",),
        )
        org_id = cur.fetchone()[0]

        # Dept
        cur.execute(
            "INSERT INTO departments (org_id, name, max_level) VALUES (%s, 'Finance', 3) RETURNING id",
            (org_id,),
        )
        dept_id = cur.fetchone()[0]

        # User
        cur.execute(
            """INSERT INTO users (org_id, idp_subject, email, display_name, role, status)
               VALUES (%s, %s, %s, 'Delete Tester', 'user', 'active') RETURNING id""",
            (org_id, f"sub-{uuid.uuid4().hex[:8]}", f"del-{uuid.uuid4().hex[:8]}@example.com"),
        )
        user_id = cur.fetchone()[0]

        # Source
        cur.execute(
            """INSERT INTO sources (org_id, kind, display_name, department_id, hierarchy_level, created_by)
               VALUES (%s, 'upload', 'Financial Audit', %s, 1, %s) RETURNING id""",
            (org_id, dept_id, user_id),
        )
        source_id = cur.fetchone()[0]

        # Document
        cur.execute(
            """INSERT INTO documents (org_id, source_id, title, mime_type, content_hash)
               VALUES (%s, %s, 'Audit Report', 'text/plain', 'hash-1234') RETURNING id""",
            (org_id, source_id),
        )
        doc_id = cur.fetchone()[0]

        # 3 Chunks
        for idx in range(3):
            # 768-dim dummy vector
            emb_str = f"[{','.join('0.01' for _ in range(768))}]"
            cur.execute(
                """INSERT INTO chunks (
                       org_id, document_id, source_id, department_id,
                       hierarchy_level, ordinal, content, token_count, embedding
                   ) VALUES (
                       %s, %s, %s, %s, 1, %s, %s, 20, CAST(%s AS vector)
                   )""",
                (org_id, doc_id, source_id, dept_id, idx, f"Chunk {idx} content", emb_str),
            )

    migrate_conn.commit()
    return org_id, user_id, source_id


@pytest.mark.integration
@pytest.mark.asyncio
async def test_verifiable_source_hard_deletion(
    migrated_db: dict[str, Any],
    setup_test_engine: str,
    migrate_conn: Any,
) -> None:
    """Deleting a source removes all chunks/documents and writes audit event."""
    org_id, user_id, source_id = _seed_populated_source(migrate_conn)

    # Verify chunks exist before deletion
    with migrate_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM chunks WHERE source_id = %s", (str(source_id),))
        initial_chunks = cur.fetchone()[0]
        assert initial_chunks == 3

    pipeline = IngestionPipeline(
        inference_provider=MockProvider(dimensions=768),
        settings=settings,
    )

    result = await pipeline.delete_source(org_id=org_id, source_id=source_id, actor_user_id=user_id)

    assert result.verified is True
    assert result.deleted_chunks == 3
    assert result.audit_log_id is not None

    # Assert complete database purging
    with migrate_conn.cursor() as cur:
        # 1. Chunks count must be 0
        cur.execute("SELECT count(*) FROM chunks WHERE source_id = %s", (str(source_id),))
        remaining_chunks = cur.fetchone()[0]
        assert remaining_chunks == 0

        # 2. Documents count must be 0
        cur.execute("SELECT count(*) FROM documents WHERE source_id = %s", (str(source_id),))
        remaining_docs = cur.fetchone()[0]
        assert remaining_docs == 0

        # 3. Source record must be deleted
        cur.execute("SELECT count(*) FROM sources WHERE id = %s", (str(source_id),))
        remaining_sources = cur.fetchone()[0]
        assert remaining_sources == 0

        # 4. Audit log contains deletion_verified event
        cur.execute(
            "SELECT event_type, payload FROM audit_log WHERE id = %s",
            (result.audit_log_id,),
        )
        audit_row = cur.fetchone()
        assert audit_row is not None
        assert audit_row[0] == "deletion_verified"
        payload = audit_row[1]
        if isinstance(payload, str):
            payload = json.loads(payload)
        assert payload["source_id"] == str(source_id)
        assert payload["remaining_chunks"] == 0
        assert payload["deleted_chunks"] == 3
