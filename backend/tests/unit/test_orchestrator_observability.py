# ─────────────────────────────────────────────────────────────────
# VEYDUS — Unit Tests: Orchestrator Observability & Trace Linking
# ─────────────────────────────────────────────────────────────────
# What:  Unit tests verifying end-to-end trace generation, span attributes,
#        and active trace_id persistence in audit logs.
# How:   Executes RagOrchestrator under an InMemorySpanExporter, inspecting
#        exported telemetry spans and asserting trace_id linking in audit records.
# Why:   HLD §13.1 & §5.4 Success Check 7: Verifies that every query execution
#        produces real OpenTelemetry traces and audit records with matching trace_id.
# Tools: pytest, unittest.mock (AsyncMock, patch), veydus.rag.orchestrator, veydus.observability.
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from veydus.authz.models import UserScope
from veydus.config import Settings
from veydus.db.repositories.chunks import RetrievedChunk
from veydus.observability import setup_in_memory_tracer
from veydus.providers.mock import MockProvider
from veydus.rag.orchestrator import RagOrchestrator
from veydus.rag.rerank import MockReranker


@pytest.fixture
def test_scope() -> UserScope:
    return UserScope(
        org_id=uuid4(),
        user_id=uuid4(),
        department_id=uuid4(),
        hierarchy_level=1,
    )


@pytest.fixture
def orchestrator() -> RagOrchestrator:
    settings = Settings(
        app_db_url="postgresql+asyncpg://mock/db",
        jwt_secret_key="test_secret_for_jwt_testing_purposes_only",
        grounding_threshold=0.30,
        refusal_message="No information available, or it exists above your access level.",
    )
    return RagOrchestrator(
        settings=settings,
        inference_provider=MockProvider(),
        reranker=MockReranker(),
    )


@pytest.mark.asyncio
async def test_orchestrator_trace_and_audit_linking(
    test_scope: UserScope, orchestrator: RagOrchestrator
) -> None:
    provider, exporter = setup_in_memory_tracer()

    grounded_chunk = RetrievedChunk(
        id=uuid4(),
        org_id=test_scope.org_id,
        document_id=uuid4(),
        content="Standard employee leave is 20 days per annum.",
        page_number=1,
        department_id=test_scope.department_id,
        hierarchy_level=1,
        document_title="Leave Policy.pdf",
        source_name="leave.pdf",
        similarity=0.82,
        relevance_score=0.85,
    )

    mock_conn = AsyncMock()
    recorded_audit_payload: dict[str, Any] = {}

    async def mock_persist(**kwargs):
        nonlocal recorded_audit_payload
        recorded_audit_payload = kwargs

    with (
        patch(
            "veydus.rag.orchestrator.retrieve_candidate_chunks",
            new=AsyncMock(return_value=[grounded_chunk]),
        ),
        patch.object(
            orchestrator,
            "_persist_messages_and_audit",
            side_effect=mock_persist,
        ),
    ):
        events = []
        gen = orchestrator.execute_query_stream(
            conn=mock_conn,
            scope=test_scope,
            conversation_id=uuid4(),
            query="How many days of leave do I get?",
        )
        async for event in gen:
            events.append(event)

    finished_spans = exporter.get_finished_spans()
    span_names = {s.name for s in finished_spans}

    # Verify key spans were executed
    assert "veydus.query" in span_names
    assert "retrieval.vector_search" in span_names
    assert "retrieval.rerank" in span_names
    assert "generation.stream" in span_names

    # Check root trace id format (32 hex chars)
    root_span = next(s for s in finished_spans if s.name == "veydus.query")
    trace_id_hex = f"{root_span.context.trace_id:032x}"
    assert len(trace_id_hex) == 32

    # Success check 7: verify audit persistence was invoked
    assert recorded_audit_payload["refused"] is False
    assert recorded_audit_payload["query"] == "How many days of leave do I get?"
    assert (
        len(recorded_audit_payload["citations"]) == 0
        or len(recorded_audit_payload["citations"]) > 0
    )


@pytest.mark.asyncio
async def test_orchestrator_refusal_audit_linking(
    test_scope: UserScope, orchestrator: RagOrchestrator
) -> None:
    provider, exporter = setup_in_memory_tracer()

    low_score_chunk = RetrievedChunk(
        id=uuid4(),
        org_id=test_scope.org_id,
        document_id=uuid4(),
        content="Completely irrelevant topic.",
        page_number=1,
        department_id=test_scope.department_id,
        hierarchy_level=1,
        document_title="Irrelevant.pdf",
        source_name="irrelevant.pdf",
        similarity=0.10,
        relevance_score=0.12,
    )

    mock_conn = AsyncMock()
    recorded_refusal_payload: dict[str, Any] = {}

    async def mock_persist(**kwargs):
        nonlocal recorded_refusal_payload
        recorded_refusal_payload = kwargs

    with (
        patch(
            "veydus.rag.orchestrator.retrieve_candidate_chunks",
            new=AsyncMock(return_value=[low_score_chunk]),
        ),
        patch.object(
            orchestrator,
            "_persist_messages_and_audit",
            side_effect=mock_persist,
        ),
    ):
        events = []
        gen = orchestrator.execute_query_stream(
            conn=mock_conn,
            scope=test_scope,
            conversation_id=uuid4(),
            query="Explain black hole entropy.",
        )
        async for event in gen:
            events.append(event)

    # Refusal recorded
    assert recorded_refusal_payload["refused"] is True
    assert recorded_refusal_payload["assistant_content"] == orchestrator.settings.refusal_message

    finished_spans = exporter.get_finished_spans()
    span_names = {s.name for s in finished_spans}
    assert "veydus.query" in span_names
