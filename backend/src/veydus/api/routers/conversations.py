# ─────────────────────────────────────────────────────────────────
# VEYDUS — Conversation Management & SSE Streaming Router
# ─────────────────────────────────────────────────────────────────
# What:  FastAPI router providing conversation CRUD endpoints and real-time
#        Server-Sent Events (SSE) RAG query response streaming (HLD §11.7).
# How:   - POST /conversations: Initializes a new conversation session.
#        - GET /conversations: Lists active conversations for the authenticated user.
#        - GET /conversations/{id}: Fetches conversation metadata and message turns.
#        - POST /conversations/{id}/messages: Executes RagOrchestrator within a
#          tenant_transaction and yields SSE formatted event payloads:
#          metadata -> token* -> citations -> done.
# Why:   HLD §11.7 requirement: Interactive token streaming interface enforcing
#        tenant boundaries, out-of-scope assertion checks, and fail-closed errors.
# Tools: FastAPI (APIRouter, Depends, HTTPException, status),
#        starlette.responses.StreamingResponse, pydantic v2, uuid.UUID,
#        veydus.db.session.tenant_transaction, veydus.rag.orchestrator.
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from starlette.responses import StreamingResponse

from veydus.auth.dependencies import get_current_user_scope
from veydus.config import settings
from veydus.db.session import tenant_transaction
from veydus.providers.base import get_inference_provider
from veydus.rag.orchestrator import AccessFilterFailure, RagOrchestrator
from veydus.rag.rerank import RerankerFailure

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from veydus.authz.models import UserScope

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/conversations", tags=["conversations"])


class CreateConversationRequest(BaseModel):
    title: str | None = Field(default=None, description="Optional title for the conversation")


class ConversationSummary(BaseModel):
    id: UUID
    org_id: UUID
    user_id: UUID
    title: str | None
    status: str
    created_at: str
    updated_at: str


class MessageItem(BaseModel):
    id: UUID
    ordinal: int
    role: str
    content: str
    citations: list[dict[str, Any]]
    refused: bool
    created_at: str


class ConversationDetail(BaseModel):
    conversation: ConversationSummary
    messages: list[MessageItem]


class QueryMessageRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000, description="User question or prompt")


@router.post("", response_model=ConversationSummary, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    body: CreateConversationRequest,
    scope: UserScope = Depends(get_current_user_scope),
) -> ConversationSummary:
    """Creates a new conversation session for the authenticated user."""
    conv_id = uuid4()
    title = body.title or "New Conversation"

    async with tenant_transaction(scope.org_id) as conn:
        stmt = text(
            """
            INSERT INTO conversations (id, org_id, user_id, title, scope_version, status)
            VALUES (:id, :org_id, :user_id, :title, 1, 'active')
            RETURNING id, org_id, user_id, title, status, created_at, updated_at
            """
        )
        result = await conn.execute(
            stmt,
            {
                "id": conv_id,
                "org_id": scope.org_id,
                "user_id": scope.user_id,
                "title": title,
            },
        )
        row = result.fetchone()
        if not row:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create conversation",
            )

        return ConversationSummary(
            id=row.id,
            org_id=row.org_id,
            user_id=row.user_id,
            title=row.title,
            status=row.status,
            created_at=row.created_at.isoformat(),
            updated_at=row.updated_at.isoformat(),
        )


@router.get("", response_model=list[ConversationSummary])
async def list_conversations(
    scope: UserScope = Depends(get_current_user_scope),
) -> list[ConversationSummary]:
    """Lists conversations for the authenticated user and org."""
    async with tenant_transaction(scope.org_id) as conn:
        stmt = text(
            """
            SELECT id, org_id, user_id, title, status, created_at, updated_at
            FROM conversations
            WHERE user_id = :user_id AND status = 'active'
            ORDER BY updated_at DESC
            LIMIT 50
            """
        )
        result = await conn.execute(stmt, {"user_id": scope.user_id})
        rows = result.fetchall()

        return [
            ConversationSummary(
                id=r.id,
                org_id=r.org_id,
                user_id=r.user_id,
                title=r.title,
                status=r.status,
                created_at=r.created_at.isoformat(),
                updated_at=r.updated_at.isoformat(),
            )
            for r in rows
        ]


@router.get("/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: UUID,
    scope: UserScope = Depends(get_current_user_scope),
) -> ConversationDetail:
    """Retrieves conversation details and prior message history."""
    async with tenant_transaction(scope.org_id) as conn:
        conv_stmt = text(
            """
            SELECT id, org_id, user_id, title, status, created_at, updated_at
            FROM conversations
            WHERE id = :id AND user_id = :user_id
            """
        )
        conv_res = await conn.execute(conv_stmt, {"id": conversation_id, "user_id": scope.user_id})
        conv_row = conv_res.fetchone()
        if not conv_row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Conversation not found",
            )

        conv_summary = ConversationSummary(
            id=conv_row.id,
            org_id=conv_row.org_id,
            user_id=conv_row.user_id,
            title=conv_row.title,
            status=conv_row.status,
            created_at=conv_row.created_at.isoformat(),
            updated_at=conv_row.updated_at.isoformat(),
        )

        msg_stmt = text(
            """
            SELECT id, ordinal, role, content, citations, refused, created_at
            FROM messages
            WHERE conversation_id = :conv_id
            ORDER BY ordinal ASC
            """
        )
        msg_res = await conn.execute(msg_stmt, {"conv_id": conversation_id})
        msg_rows = msg_res.fetchall()

        messages = [
            MessageItem(
                id=m.id,
                ordinal=m.ordinal,
                role=m.role,
                content=m.content,
                citations=m.citations if isinstance(m.citations, list) else [],
                refused=m.refused,
                created_at=m.created_at.isoformat(),
            )
            for m in msg_rows
        ]

        return ConversationDetail(
            conversation=conv_summary,
            messages=messages,
        )


@router.post("/{conversation_id}/messages")
async def send_message_stream(
    conversation_id: UUID,
    body: QueryMessageRequest,
    scope: UserScope = Depends(get_current_user_scope),
) -> StreamingResponse:
    """Submits a query to the RAG pipeline and streams response tokens via Server-Sent Events."""
    # First verify conversation exists and belongs to user
    async with tenant_transaction(scope.org_id) as conn:
        chk_stmt = text(
            "SELECT id FROM conversations WHERE id = :id AND user_id = :user_id AND status = 'active'"
        )
        chk_res = await conn.execute(chk_stmt, {"id": conversation_id, "user_id": scope.user_id})
        if not chk_res.fetchone():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Active conversation not found",
            )

        # Retrieve prior turn history (last 5 turns)
        hist_stmt = text(
            """
            SELECT role, content
            FROM messages
            WHERE conversation_id = :conv_id
            ORDER BY ordinal DESC
            LIMIT 10
            """
        )
        hist_res = await conn.execute(hist_stmt, {"conv_id": conversation_id})
        hist_rows = list(reversed(hist_res.fetchall()))
        chat_history = [{"role": r.role, "content": r.content} for r in hist_rows]

    # Instantiate orchestrator components
    provider = get_inference_provider(settings)
    orchestrator = RagOrchestrator(
        settings=settings,
        inference_provider=provider,
    )

    async def event_generator() -> AsyncIterator[str]:
        """Inner streaming generator bound to tenant database transaction."""
        try:
            async with tenant_transaction(scope.org_id) as conn:
                stream = orchestrator.execute_query_stream(
                    conn=conn,
                    scope=scope,
                    conversation_id=conversation_id,
                    query=body.query,
                    chat_history=chat_history,
                )
                async for event in stream:
                    yield event.to_sse()
        except (RerankerFailure, AccessFilterFailure) as exc:
            logger.critical("Security gate failure during stream generation: %s", exc)
            yield f'event: error\ndata: {{"error": "SecurityGateFailure", "message": "{exc}"}}\n\n'
        except Exception as exc:
            logger.exception("Unexpected error in RAG stream generation: %s", exc)
            yield f'event: error\ndata: {{"error": "InternalServerError", "message": "{exc}"}}\n\n'

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
