# ─────────────────────────────────────────────────────────────────
# VEYDUS — Integration Tests: End-to-End Observability & Tracing
# ─────────────────────────────────────────────────────────────────
# What:  Integration tests verifying end-to-end OpenTelemetry distributed trace
#        generation, span attributes, Never-Log scrubbing, and audit log trace ID linking.
# How:   Executes real HTTP requests via httpx.AsyncClient under an InMemorySpanExporter,
#        inspecting exported telemetry spans, verifying span name completeness,
#        confirming zero chunk content in span attributes, and checking audit records.
# Why:   HLD §13.1, §13.3 & §5.4 mandate that every API query produces canonical
#        spans without leaking confidential chunk or query text, and links trace_id to audit.
# Tools: pytest, pytest-asyncio, httpx.AsyncClient, ASGITransport, veydus.api.main, veydus.observability.
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

import json
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from veydus.api.main import app
from veydus.config import settings
from veydus.observability import (
    reset_tracer_provider,
    setup_in_memory_tracer,
)


@pytest.fixture
def seeded_observability_tenant(migrate_conn):
    """Seeds an org, department, user, document, and chunk for observability testing."""
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
        # Org
        cur.execute(
            "INSERT INTO organizations (id, name, slug) VALUES (%s, %s, %s)",
            (org_id, "Observability Org", "obs-org"),
        )
        # Department
        cur.execute(
            "INSERT INTO departments (id, org_id, name, max_level) VALUES (%s, %s, %s, 3)",
            (dept_id, org_id, "Engineering"),
        )
        # User
        cur.execute(
            """
            INSERT INTO users (id, org_id, idp_subject, email, display_name, role, status)
            VALUES (%s, %s, %s, 'dev@obs.com', 'Dev User', 'user', 'active')
            """,
            (user_id, org_id, f"sub-{user_id[:8]}"),
        )
        # Access Grant
        cur.execute(
            """
            INSERT INTO access_grants (user_id, org_id, department_id, hierarchy_level, granted_by)
            VALUES (%s, %s, %s, 1, %s)
            """,
            (user_id, org_id, dept_id, user_id),
        )
        # Source
        cur.execute(
            """
            INSERT INTO sources (id, org_id, kind, display_name, department_id, hierarchy_level, created_by)
            VALUES (%s, %s, 'upload', 'Engineering Docs', %s, 1, %s)
            """,
            (src_id, org_id, dept_id, user_id),
        )
        # Document
        cur.execute(
            """
            INSERT INTO documents (id, org_id, source_id, title, mime_type, content_hash)
            VALUES (%s, %s, %s, 'Architecture Spec', 'application/pdf', 'hash-obs-spec')
            """,
            (doc_id, org_id, src_id),
        )
        # Chunk
        cur.execute(
            """
            INSERT INTO chunks (id, org_id, document_id, source_id, ordinal, content, token_count, department_id, hierarchy_level, embedding)
            VALUES (%s, %s, %s, %s, 1, 'Architecture spec: system uses microservices architecture with pgvector.', 15, %s, 1, %s::vector)
            """,
            (chunk_id, org_id, doc_id, src_id, dept_id, vec_str),
        )

    migrate_conn.commit()

    return {
        "org_id": org_id,
        "dept_id": dept_id,
        "user_id": user_id,
        "document_id": doc_id,
        "chunk_id": chunk_id,
    }


@pytest.mark.integration
@pytest.mark.asyncio
async def test_observability_e2e_tracing_and_audit(
    setup_test_engine, seeded_observability_tenant, migrate_conn
) -> None:
    """Verifies that an end-to-end query generates spans and links trace_id in audit_log."""
    settings.inference_provider = "mock"
    provider, exporter = setup_in_memory_tracer()

    data = seeded_observability_tenant

    headers = {
        "X-Org-Id": data["org_id"],
        "X-User-Id": data["user_id"],
        "X-Department-Id": data["dept_id"],
        "X-Hierarchy-Level": "1",
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Create conversation
        conv_resp = await client.post(
            "/conversations",
            headers=headers,
            json={"title": "Observability Tracing Test"},
        )
        assert conv_resp.status_code == 201
        conversation_id = conv_resp.json()["id"]

        # 2. Execute RAG query stream
        query_text = "What is the system architecture based on engineering docs?"
        stream_resp = await client.post(
            f"/conversations/{conversation_id}/messages",
            headers=headers,
            json={"query": query_text},
        )
        assert stream_resp.status_code == 200
        stream_content = stream_resp.text
        assert "event: metadata" in stream_content
        assert "event: token" in stream_content

    # 3. Inspect exported OpenTelemetry spans
    finished_spans = exporter.get_finished_spans()
    span_names = {s.name for s in finished_spans}

    assert "veydus.query" in span_names
    assert "retrieval.vector_search" in span_names
    assert "retrieval.rerank" in span_names

    root_span = next(s for s in finished_spans if s.name == "veydus.query")
    trace_id_hex = f"{root_span.context.trace_id:032x}"
    assert len(trace_id_hex) == 32

    # 4. Check Never-Log invariant in span attributes
    for span in finished_spans:
        for attr_key, attr_val in (span.attributes or {}).items():
            attr_key_lower = attr_key.lower()
            assert "content" not in attr_key_lower
            assert "query" not in attr_key_lower or attr_key_lower in {
                "query_rewritten",
                "query_length",
            }
            assert "embedding" not in attr_key_lower
            val_str = str(attr_val)
            assert query_text not in val_str
            assert "microservices architecture" not in val_str

    # 5. Verify audit_log row recorded the matching trace_id
    with migrate_conn.cursor() as cur:
        cur.execute(
            "SELECT trace_id, event_type FROM audit_log WHERE org_id = %s ORDER BY created_at DESC LIMIT 1",
            (data["org_id"],),
        )
        row = cur.fetchone()
        assert row is not None
        audit_trace_id, event_type = row
        assert audit_trace_id == trace_id_hex
        assert event_type == "rag_query_completed"

    reset_tracer_provider()
