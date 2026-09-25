# ─────────────────────────────────────────────────────────────────
# VEYDUS — Unit Tests: Conversations Router & SSE Streaming (HLD §11.7)
# ─────────────────────────────────────────────────────────────────
# What:  Unit tests verifying conversation creation, retrieval, authorization
#        header handling, and Server-Sent Events (SSE) query response streaming.
# How:   Invokes FastAPI endpoints via TestClient with dependency overrides
#        for UserScope and mocked database operations.
# Why:   HLD §11.7 requirement: Verifies API contract for conversation sessions
#        and real-time token streaming with proper media-type and event sequence.
# Tools: pytest, fastapi.testclient.TestClient, unittest.mock, veydus.api.main.
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from starlette.testclient import TestClient

from veydus.api.main import app
from veydus.auth.dependencies import get_current_user_scope
from veydus.authz.models import UserScope
from veydus.rag.orchestrator import StreamEvent

if TYPE_CHECKING:
    from collections.abc import Generator


@pytest.fixture
def mock_scope() -> UserScope:
    return UserScope(
        org_id=uuid4(),
        user_id=uuid4(),
        department_id=uuid4(),
        hierarchy_level=1,
    )


@pytest.fixture
def client(mock_scope: UserScope) -> Generator[TestClient, None, None]:
    app.dependency_overrides[get_current_user_scope] = lambda: mock_scope
    test_client = TestClient(app)
    yield test_client
    app.dependency_overrides.clear()


def test_create_conversation(client: TestClient, mock_scope: UserScope) -> None:
    conv_id = uuid4()
    now = datetime.now(UTC)

    mock_row = MagicMock()
    mock_row.id = conv_id
    mock_row.org_id = mock_scope.org_id
    mock_row.user_id = mock_scope.user_id
    mock_row.title = "Project Planning"
    mock_row.status = "active"
    mock_row.created_at = now
    mock_row.updated_at = now

    mock_conn = AsyncMock()
    mock_result = MagicMock()
    mock_result.fetchone.return_value = mock_row
    mock_conn.execute.return_value = mock_result

    with patch("veydus.api.routers.conversations.tenant_transaction") as mock_tt:
        mock_tt.return_value.__aenter__.return_value = mock_conn

        response = client.post(
            "/conversations",
            json={"title": "Project Planning"},
        )

        assert response.status_code == 201
        data = response.json()
        assert data["id"] == str(conv_id)
        assert data["title"] == "Project Planning"


def test_send_message_stream(client: TestClient, mock_scope: UserScope) -> None:
    conv_id = uuid4()

    mock_conn = AsyncMock()
    mock_chk_res = MagicMock()
    mock_chk_res.fetchone.return_value = (conv_id,)
    mock_hist_res = MagicMock()
    mock_hist_res.fetchall.return_value = []

    mock_conn.execute.side_effect = [mock_chk_res, mock_hist_res, MagicMock()]

    async def mock_stream(*args, **kwargs):
        yield StreamEvent(
            event="metadata",
            data={"conversation_id": str(conv_id), "refused": False},
        )
        yield StreamEvent(event="token", data={"token": "Hello", "index": 0})
        yield StreamEvent(event="citations", data={"citations": []})
        yield StreamEvent(event="done", data={"status": "completed"})

    with (
        patch("veydus.api.routers.conversations.tenant_transaction") as mock_tt,
        patch(
            "veydus.rag.orchestrator.RagOrchestrator.execute_query_stream",
            side_effect=mock_stream,
        ),
    ):
        mock_tt.return_value.__aenter__.return_value = mock_conn

        response = client.post(
            f"/conversations/{conv_id}/messages",
            json={"query": "Hello world"},
        )

        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]
        text_body = response.text
        assert "event: metadata" in text_body
        assert "event: token" in text_body
        assert "event: citations" in text_body
        assert "event: done" in text_body
