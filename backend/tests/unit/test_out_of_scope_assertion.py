# ─────────────────────────────────────────────────────────────────
# VEYDUS — Unit Tests: Out-of-Scope Assertion Gate (HLD §8.6)
# ─────────────────────────────────────────────────────────────────
# What:  Unit tests verifying that any candidate chunk violating user access
#        scope (cross-tenant, cross-department, or higher hierarchy level)
#        is intercepted by the post-rerank assertion gate.
# How:   Injects synthetic chunks with mismatched scope attributes into
#        RagOrchestrator and asserts that AccessFilterFailure is raised,
#        preventing any LLM generation.
# Why:   HLD §8.6 Security Invariant: Defense-in-depth gate ensuring that even
#        if database retrieval filters suffered an unexpected malfunction,
#        unauthorized enterprise data is never incorporated into LLM prompts.
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
from veydus.rag.orchestrator import AccessFilterFailure, RagOrchestrator
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
    )
    return RagOrchestrator(
        settings=settings,
        inference_provider=MockProvider(),
        reranker=MockReranker(),
    )


@pytest.mark.asyncio
async def test_out_of_scope_hierarchy_level_intercepted(
    test_scope: UserScope, orchestrator: RagOrchestrator
) -> None:
    # Chunk has hierarchy level 3, but user is level 1
    violating_chunk = RetrievedChunk(
        id=uuid4(),
        org_id=test_scope.org_id,
        document_id=uuid4(),
        content="Confidential executive compensation data",
        page_number=1,
        department_id=test_scope.department_id,
        hierarchy_level=3,
        document_title="Executive Comp.pdf",
        source_name="exec_comp.pdf",
        similarity=0.92,
        relevance_score=0.95,
    )

    with (
        patch(
            "veydus.rag.orchestrator.retrieve_candidate_chunks",
            new=AsyncMock(return_value=[violating_chunk]),
        ),
        pytest.raises(AccessFilterFailure),
    ):
        gen = orchestrator.execute_query_stream(
            conn=AsyncMock(),
            scope=test_scope,
            conversation_id=uuid4(),
            query="What are executive bonuses?",
        )
        async for _ in gen:
            pass


@pytest.mark.asyncio
async def test_out_of_scope_cross_department_intercepted(
    test_scope: UserScope, orchestrator: RagOrchestrator
) -> None:
    # Chunk has a different department ID
    violating_chunk = RetrievedChunk(
        id=uuid4(),
        org_id=test_scope.org_id,
        document_id=uuid4(),
        content="Legal audit findings",
        page_number=1,
        department_id=uuid4(),  # Different department
        hierarchy_level=1,
        document_title="Legal Audit.pdf",
        source_name="legal_audit.pdf",
        similarity=0.90,
        relevance_score=0.91,
    )

    with (
        patch(
            "veydus.rag.orchestrator.retrieve_candidate_chunks",
            new=AsyncMock(return_value=[violating_chunk]),
        ),
        pytest.raises(AccessFilterFailure),
    ):
        gen = orchestrator.execute_query_stream(
            conn=AsyncMock(),
            scope=test_scope,
            conversation_id=uuid4(),
            query="Show legal findings",
        )
        async for _ in gen:
            pass
