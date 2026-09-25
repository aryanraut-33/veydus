# ─────────────────────────────────────────────────────────────────
# VEYDUS — Unit Tests: Reranker Fail-Closed Gate (HLD §15)
# ─────────────────────────────────────────────────────────────────
# What:  Unit tests verifying that cross-encoder reranker failures result in
#        immediate fail-closed abortion, preventing unranked vector candidates
#        from reaching prompt assembly or LLM generation.
# How:   Simulates vector retrieval returning candidate chunks, then triggers
#        a RerankerFailure inside MockReranker. Verifies that RagOrchestrator
#        re-raises the exception and never calls inference_provider.generate_stream().
# Why:   HLD §15 mandate: Reranking is MANDATORY and MUST FAIL CLOSED.
#        Zero tolerance for unranked or unverified candidate leakage.
# Tools: pytest, unittest.mock (AsyncMock), uuid.uuid4, veydus.rag.orchestrator.
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
from veydus.rag.rerank import MockReranker, RerankerFailure


@pytest.fixture
def test_scope() -> UserScope:
    return UserScope(
        org_id=uuid4(),
        user_id=uuid4(),
        department_id=uuid4(),
        hierarchy_level=1,
    )


@pytest.mark.asyncio
async def test_reranker_failure_fails_closed(test_scope: UserScope) -> None:
    settings = Settings(
        app_db_url="postgresql+asyncpg://mock/db",
        jwt_secret_key="test_secret_for_jwt_testing_purposes_only",
        grounding_threshold=0.30,
    )
    provider = MockProvider()
    failing_reranker = MockReranker(should_fail=True)
    orchestrator = RagOrchestrator(
        settings=settings,
        inference_provider=provider,
        reranker=failing_reranker,
    )

    mock_conn = AsyncMock()
    chunk = RetrievedChunk(
        id=uuid4(),
        org_id=test_scope.org_id,
        document_id=uuid4(),
        content="Test content",
        page_number=1,
        department_id=test_scope.department_id,
        hierarchy_level=1,
        document_title="Test Doc",
        source_name="test.pdf",
        similarity=0.85,
    )

    with (
        patch(
            "veydus.rag.orchestrator.retrieve_candidate_chunks",
            new=AsyncMock(return_value=[chunk]),
        ),
        patch.object(
            provider,
            "generate_stream",
            wraps=provider.generate_stream,
        ) as mock_generate_stream,
    ):
        # Pipeline must raise RerankerFailure when reranking fails
        with pytest.raises(RerankerFailure):
            gen = orchestrator.execute_query_stream(
                conn=mock_conn,
                scope=test_scope,
                conversation_id=uuid4(),
                query="What is the test policy?",
            )
            async for _ in gen:
                pass

        # Inference provider streaming must NEVER have been called
        mock_generate_stream.assert_not_called()
