# ─────────────────────────────────────────────────────────────────
# VEYDUS — Unit Tests: Grounding Evaluation Gate (HLD §8.3)
# ─────────────────────────────────────────────────────────────────
# What:  Unit tests verifying that queries with relevance scores below the
#        configured grounding threshold (0.30) emit the standard refusal
#        message verbatim without invoking LLM generation.
# How:   Mocks candidate retrieval and reranking with scores above and below 0.30.
#        Checks SSE event sequences for refused=true metadata and refusal token.
# Why:   HLD §8.3 requirement: System must refuse queries when no retrieved
#        passages are sufficiently grounded, saving inference cost and preventing
#        unfounded hallucinations.
# Tools: pytest, unittest.mock, uuid.uuid4, veydus.rag.orchestrator.
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from veydus.authz.models import UserScope
from veydus.config import Settings
from veydus.db.repositories.chunks import RetrievedChunk
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
async def test_below_threshold_emits_refusal_without_inference(
    test_scope: UserScope, orchestrator: RagOrchestrator
) -> None:
    # Chunk with relevance score 0.22 (< 0.30)
    low_relevance_chunk = RetrievedChunk(
        id=uuid4(),
        org_id=test_scope.org_id,
        document_id=uuid4(),
        content="Irrelevant background noise",
        page_number=1,
        department_id=test_scope.department_id,
        hierarchy_level=1,
        document_title="Noise.pdf",
        source_name="noise.pdf",
        similarity=0.20,
        relevance_score=0.22,
    )

    mock_conn = AsyncMock()
    with (
        patch(
            "veydus.rag.orchestrator.retrieve_candidate_chunks",
            new=AsyncMock(return_value=[low_relevance_chunk]),
        ),
        patch.object(
            orchestrator.inference_provider,
            "generate_stream",
            wraps=orchestrator.inference_provider.generate_stream,
        ) as mock_generate_stream,
        patch.object(
            orchestrator,
            "_persist_messages_and_audit",
            new=AsyncMock(),
        ),
    ):
        events = []
        gen = orchestrator.execute_query_stream(
            conn=mock_conn,
            scope=test_scope,
            conversation_id=uuid4(),
            query="Tell me about quantum gravity",
        )
        async for event in gen:
            events.append(event)

        # Ensure generation stream was NEVER called
        mock_generate_stream.assert_not_called()

        # Check metadata event
        meta_event = next(e for e in events if e.event == "metadata")
        assert meta_event.data["refused"] is True
        assert meta_event.data["refusal_reason"] == "grounding_score_below_threshold"

        # Check token event contains refusal message
        token_event = next(e for e in events if e.event == "token")
        assert token_event.data["token"] == orchestrator.settings.refusal_message

        # Check done event
        done_event = next(e for e in events if e.event == "done")
        assert done_event.data["status"] == "refused"


@pytest.mark.asyncio
async def test_above_threshold_executes_generation(
    test_scope: UserScope, orchestrator: RagOrchestrator
) -> None:
    # Chunk with relevance score 0.75 (>= 0.30)
    grounded_chunk = RetrievedChunk(
        id=uuid4(),
        org_id=test_scope.org_id,
        document_id=uuid4(),
        content="The employee handbook specifies 20 vacation days annually.",
        page_number=3,
        department_id=test_scope.department_id,
        hierarchy_level=1,
        document_title="Employee Handbook.pdf",
        source_name="handbook.pdf",
        similarity=0.70,
        relevance_score=0.75,
    )

    mock_conn = AsyncMock()
    with (
        patch(
            "veydus.rag.orchestrator.retrieve_candidate_chunks",
            new=AsyncMock(return_value=[grounded_chunk]),
        ),
        patch.object(
            orchestrator.inference_provider,
            "generate_stream",
            wraps=orchestrator.inference_provider.generate_stream,
        ) as mock_generate_stream,
        patch.object(
            orchestrator,
            "_persist_messages_and_audit",
            new=AsyncMock(),
        ),
    ):
        events = []
        gen = orchestrator.execute_query_stream(
            conn=mock_conn,
            scope=test_scope,
            conversation_id=uuid4(),
            query="How many vacation days do I get?",
        )
        async for event in gen:
            events.append(event)

        # Generation stream was invoked
        mock_generate_stream.assert_called_once()

        # Metadata should be not refused
        meta_event = next(e for e in events if e.event == "metadata")
        assert meta_event.data["refused"] is False

        # Done event should be completed
        done_event = next(e for e in events if e.event == "done")
        assert done_event.data["status"] == "completed"
