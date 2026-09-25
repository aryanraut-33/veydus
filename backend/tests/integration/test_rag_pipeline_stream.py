# ─────────────────────────────────────────────────────────────────
# VEYDUS — Integration Tests: End-to-End RAG Pipeline & SSE Streaming
# ─────────────────────────────────────────────────────────────────
# What:  Integration tests verifying full conversation creation, SSE streaming
#        query execution, token re-hydration, citation verification, message
#        persistence, and immutable audit logging against PostgreSQL 16.
# How:   Executes real HTTP requests via TestClient, exercising the complete runtime
#        stack (FastAPI -> tenant_transaction -> pgvector -> RagOrchestrator -> SSE).
# Why:   HLD §11.7 & §17.3 requirement: Validates that the complete RAG runtime
#        functions end-to-end with active database transactions and RLS.
# Tools: pytest, testcontainers, httpx (AsyncClient, ASGITransport), veydus.api.main.
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

import json
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from veydus.api.main import app
from veydus.db.session import tenant_transaction


@pytest.fixture
def seeded_conversation_tenant(migrate_conn):
    """Seeds an org, department, user, document, and chunk for pipeline streaming."""
    org_id = str(uuid4())
    dept_id = str(uuid4())
    user_id = str(uuid4())
    src_id = str(uuid4())
    doc_id = str(uuid4())
    chunk_id = str(uuid4())

    vec_unit = [0.0] * 768
    vec_unit[0] = 1.0
    vec_str = json.dumps(vec_unit)

    with migrate_conn.cursor() as cur:
        # Org & Dept & User
        cur.execute(
            "INSERT INTO organizations (id, name, slug) VALUES (%s, %s, %s)",
            (org_id, "Stream Org", "stream-org"),
        )
        cur.execute(
            "INSERT INTO departments (id, org_id, name, max_level) VALUES (%s, %s, %s, 3)",
            (dept_id, org_id, "Support"),
        )
        cur.execute(
            """
            INSERT INTO users (id, org_id, idp_subject, email, display_name, role, status)
            VALUES (%s, %s, %s, 'agent@stream.com', 'Agent User', 'user', 'active')
            """,
            (user_id, org_id, f"sub-{user_id[:8]}"),
        )
        # Source & Document & Chunk
        cur.execute(
            """
            INSERT INTO sources (id, org_id, kind, display_name, department_id, hierarchy_level, created_by)
            VALUES (%s, %s, 'upload', 'Support Manual', %s, 1, %s)
            """,
            (src_id, org_id, dept_id, user_id),
        )
        cur.execute(
            """
            INSERT INTO access_grants (user_id, org_id, department_id, hierarchy_level, granted_by)
            VALUES (%s, %s, %s, 1, %s)
            """,
            (user_id, org_id, dept_id, user_id),
        )
        cur.execute(
            """
            INSERT INTO documents (id, org_id, source_id, title, mime_type, content_hash)
            VALUES (%s, %s, %s, 'Support Guidelines', 'application/pdf', 'hash-1234')
            """,
            (doc_id, org_id, src_id),
        )
        cur.execute(
            """
            INSERT INTO chunks (id, org_id, document_id, source_id, ordinal, content, token_count, department_id, hierarchy_level, embedding)
            VALUES (%s, %s, %s, %s, 0, 'Password resets require two-factor authentication.', 10, %s, 1, %s::vector)
            """,
            (chunk_id, org_id, doc_id, src_id, dept_id, vec_str),
        )

    migrate_conn.commit()

    return {
        "org_id": org_id,
        "dept_id": dept_id,
        "user_id": user_id,
        "doc_id": doc_id,
        "chunk_id": chunk_id,
    }


@pytest.mark.integration
@pytest.mark.asyncio
async def test_e2e_rag_conversation_and_stream(
    setup_test_engine, seeded_conversation_tenant
) -> None:
    from veydus.config import settings

    settings.inference_provider = "mock"
    data = seeded_conversation_tenant

    headers = {
        "X-Org-Id": data["org_id"],
        "X-User-Id": data["user_id"],
        "X-Department-Id": data["dept_id"],
        "X-Hierarchy-Level": "1",
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Create Conversation
        create_res = await client.post(
            "/conversations",
            json={"title": "Security Support Inquiries"},
            headers=headers,
        )
        assert create_res.status_code == 201
        conv_data = create_res.json()
        conv_id = conv_data["id"]

        # 2. Post Query Message Stream
        stream_res = await client.post(
            f"/conversations/{conv_id}/messages",
            json={"query": "How do password resets work?"},
            headers=headers,
        )
        assert stream_res.status_code == 200
        assert "text/event-stream" in stream_res.headers["content-type"]

        body = stream_res.text
        assert "event: metadata" in body
        assert "event: token" in body
        assert "event: citations" in body
        assert "event: done" in body

    # 3. Verify Messages Table Persistence
    from sqlalchemy import text

    async with tenant_transaction(UUID(data["org_id"])) as conn:
        msg_res = await conn.execute(
            text(
                "SELECT role, content, refused FROM messages WHERE conversation_id = :id ORDER BY ordinal ASC"
            ),
            {"id": UUID(conv_id)},
        )
        msgs = msg_res.fetchall()
        assert len(msgs) == 2
        assert msgs[0].role == "user"
        assert msgs[0].content == "How do password resets work?"
        assert msgs[1].role == "assistant"

        # 4. Verify Immutable Audit Log
        audit_res = await conn.execute(
            text(
                "SELECT event_type, payload FROM audit_log WHERE org_id = :org_id ORDER BY id DESC LIMIT 1"
            ),
            {"org_id": UUID(data["org_id"])},
        )
        audit_row = audit_res.fetchone()
        assert audit_row is not None
        assert audit_row.event_type == "rag_query_completed"
        payload = audit_row.payload
        if isinstance(payload, str):
            payload = json.loads(payload)
        assert payload["conversation_id"] == conv_id
