# ─────────────────────────────────────────────────────────────────
# VEYDUS — RAG Orchestrator & Safety Pipeline [SECURITY-CRITICAL]
# ─────────────────────────────────────────────────────────────────
# What:  Coordinates the end-to-end RAG workflow: dense embedding generation,
#        access-controlled candidate retrieval, cross-encoder reranking,
#        post-rerank out-of-scope assertion gate, grounding evaluation gate,
#        Presidio PII redaction/re-hydration, SSE stream formatting, and
#        append-only audit persistence.
# How:   - Executes vector retrieval via ChunksRepository (k=30, HNSW iterative scan).
#        - Reranks candidates via Reranker (n=6). If reranker fails, raises
#          RerankerFailure immediately to fail-closed with HTTP 500 (HLD §15).
#        - Out-of-Scope Assertion Gate: Evaluates can_access_chunk(scope, chunk.meta)
#          on all top-n chunks before prompt construction. If any chunk fails,
#          logs CRITICAL security alert and raises AccessFilterFailure (HLD §8.6).
#        - Grounding Evaluation Gate: If max relevance score < grounding_threshold (0.30),
#          aborts LLM inference and emits refusal event stream (HLD §8.3).
#        - PII Sanitization: Redacts Aadhaar/PAN/email/phone with reverse tokens;
#          re-hydrates tokens during client-side token emission.
#        - Records conversation messages and append-only audit trail.
# Why:   HLD §8 & §15 core security requirement: Defense in depth ensuring
#        zero unauthorized data leakage, strict grounding, and fail-closed safety.
# Tools: asyncio, time, uuid.UUID, pydantic v2, sqlalchemy, veydus.authz.policy,
#        veydus.db.repositories.chunks, veydus.rag.rerank, veydus.rag.redaction,
#        veydus.rag.prompt, veydus.audit.service.
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field
from sqlalchemy import text

from veydus.authz.policy import can_access_chunk
from veydus.db.repositories.chunks import RetrievedChunk, retrieve_candidate_chunks
from veydus.rag.prompt import CitationPayload, build_rag_prompt, sanitize_citations
from veydus.rag.redaction import PIIRedactor
from veydus.rag.rerank import MockReranker, Reranker

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncConnection

    from veydus.authz.models import UserScope
    from veydus.config import Settings
    from veydus.providers.base import InferenceProvider

logger = logging.getLogger(__name__)


class AccessFilterFailure(Exception):
    """Raised when an out-of-scope chunk bypasses retrieval filters (HLD §8.6)."""


class StreamEvent(BaseModel):
    """Server-Sent Event representation for streaming responses."""

    event: str = Field(description="SSE event name: metadata, token, citations, done, error")
    data: dict[str, Any] = Field(description="JSON payload serializable for SSE client delivery")

    def to_sse(self) -> str:
        """Formats the event according to W3C Server-Sent Events specification."""
        payload_str = json.dumps(self.data)
        return f"event: {self.event}\ndata: {payload_str}\n\n"


class RagOrchestrator:
    """Security-hardened enterprise RAG retrieval and generation engine."""

    def __init__(
        self,
        settings: Settings,
        inference_provider: InferenceProvider,
        reranker: Reranker | None = None,
        redactor: PIIRedactor | None = None,
    ) -> None:
        self.settings = settings
        self.inference_provider = inference_provider
        self.reranker = reranker or MockReranker()
        self.redactor = redactor or PIIRedactor(enabled=settings.pii_redaction_enabled)

    async def execute_query_stream(
        self,
        conn: AsyncConnection,
        scope: UserScope,
        conversation_id: UUID,
        query: str,
        chat_history: list[dict[str, str]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Executes full RAG pipeline and yields SSE events."""
        start_time = time.perf_counter()
        message_id = uuid4()

        # 1. PII Redaction on User Query
        query_redaction = self.redactor.redact(query)
        sanitized_query = query_redaction.redacted_text
        request_reverse_mapping = dict(query_redaction.reverse_mapping)

        # 2. Embedding Generation
        query_embeddings = await self.inference_provider.embed([sanitized_query])
        query_vector = query_embeddings[0]

        # 3. Access-Filtered Candidate Retrieval (k = 30)
        candidates = await retrieve_candidate_chunks(
            conn=conn,
            scope=scope,
            query_embedding=query_vector,
            k=self.settings.retrieval_k,
            ef_search=self.settings.retrieval_ef_search,
            max_scan_tuples=self.settings.retrieval_max_scan_tuples,
        )

        # 4. Cross-Encoder Reranking (Fail-closed gate: HLD §15)
        top_chunks = await self.reranker.rerank(
            query=sanitized_query,
            candidates=candidates,
            top_n=self.settings.rerank_n,
        )

        # 5. Out-of-Scope Assertion Gate (HLD §8.6)
        # Verify that EVERY chunk selected for generation strictly matches the user's scope
        for chunk in top_chunks:
            if not can_access_chunk(scope, chunk.meta):
                logger.critical(
                    "SECURITY INVARIANT VIOLATION: Chunk %s (org=%s, dept=%s, level=%s) "
                    "is inaccessible to User %s (org=%s, dept=%s, level=%s)!",
                    chunk.id,
                    chunk.org_id,
                    chunk.department_id,
                    chunk.hierarchy_level,
                    scope.user_id,
                    scope.org_id,
                    scope.department_id,
                    scope.hierarchy_level,
                )
                raise AccessFilterFailure(
                    f"Out-of-scope chunk {chunk.id} detected in post-rerank safety gate."
                )

        # 6. Grounding Evaluation Gate (HLD §8.3)
        # Determine highest relevance score
        max_score = 0.0
        if top_chunks:
            scores = [
                c.relevance_score if c.relevance_score is not None else c.similarity
                for c in top_chunks
            ]
            max_score = max(scores) if scores else 0.0

        is_grounded = bool(top_chunks) and (max_score >= self.settings.grounding_threshold)

        if not is_grounded:
            logger.info(
                "Query below grounding threshold (max_score=%.4f < threshold=%.2f). "
                "Emitting refusal without LLM inference.",
                max_score,
                self.settings.grounding_threshold,
            )
            # Emit Metadata event
            yield StreamEvent(
                event="metadata",
                data={
                    "conversation_id": str(conversation_id),
                    "message_id": str(message_id),
                    "model": self.settings.generation_model,
                    "refused": True,
                    "refusal_reason": "grounding_score_below_threshold",
                    "max_relevance_score": max_score,
                },
            )

            # Emit Refusal Token
            refusal_text = self.settings.refusal_message
            yield StreamEvent(
                event="token",
                data={"token": refusal_text, "index": 0},
            )

            # Emit Empty Citations
            yield StreamEvent(event="citations", data={"citations": []})

            # Record refusal message and audit row
            total_duration_ms = int((time.perf_counter() - start_time) * 1000)
            await self._persist_messages_and_audit(
                conn=conn,
                scope=scope,
                conversation_id=conversation_id,
                message_id=message_id,
                query=query,
                assistant_content=refusal_text,
                citations=[],
                refused=True,
                latency_ms=total_duration_ms,
            )

            # Emit Done event
            yield StreamEvent(
                event="done",
                data={
                    "status": "refused",
                    "total_tokens": 1,
                    "latency_ms": total_duration_ms,
                },
            )
            return

        # 7. Redact Context Chunks & Build Reverse Mapping
        sanitized_chunks: list[RetrievedChunk] = []
        for c in top_chunks:
            chunk_redaction = self.redactor.redact(c.content)
            request_reverse_mapping.update(chunk_redaction.reverse_mapping)
            sanitized_chunks.append(c.model_copy(update={"content": chunk_redaction.redacted_text}))

        # 8. Prompt Construction with Anti-Injection Framing
        prompt_assembly = build_rag_prompt(
            query=sanitized_query,
            chunks=sanitized_chunks,
            refusal_message=self.settings.refusal_message,
            chat_history=chat_history,
        )

        # Emit Initial Metadata event
        yield StreamEvent(
            event="metadata",
            data={
                "conversation_id": str(conversation_id),
                "message_id": str(message_id),
                "model": self.settings.generation_model,
                "refused": False,
                "candidate_chunks": len(candidates),
                "reranked_chunks": len(top_chunks),
            },
        )

        # 9. LLM Stream Generation & Token Re-hydration
        accumulated_raw_tokens: list[str] = []
        token_index = 0
        ttft_ms: int | None = None

        stream_gen = self.inference_provider.generate_stream(
            prompt=prompt_assembly.user_prompt,
            system_prompt=prompt_assembly.system_prompt,
        )

        async for raw_token in stream_gen:
            if ttft_ms is None:
                ttft_ms = int((time.perf_counter() - start_time) * 1000)

            accumulated_raw_tokens.append(raw_token)
            # Rehydrate sensitive PII tokens on-the-fly for client delivery
            hydrated_token = self.redactor.rehydrate(raw_token, request_reverse_mapping)

            yield StreamEvent(
                event="token",
                data={"token": hydrated_token, "index": token_index},
            )
            token_index += 1

        full_raw_response = "".join(accumulated_raw_tokens)
        full_hydrated_response = self.redactor.rehydrate(full_raw_response, request_reverse_mapping)

        # 10. Citation Sanitization & Anomaly Stripping
        clean_response, citations = sanitize_citations(
            text=full_hydrated_response,
            chunk_mapping=prompt_assembly.chunk_mapping,
        )

        # Emit Citations event
        yield StreamEvent(
            event="citations",
            data={"citations": [c.model_dump(mode="json") for c in citations]},
        )

        total_duration_ms = int((time.perf_counter() - start_time) * 1000)

        # 11. Persist Messages and Append-Only Audit Log
        await self._persist_messages_and_audit(
            conn=conn,
            scope=scope,
            conversation_id=conversation_id,
            message_id=message_id,
            query=query,
            assistant_content=clean_response,
            citations=citations,
            refused=False,
            latency_ms=total_duration_ms,
        )

        # Emit Done event
        yield StreamEvent(
            event="done",
            data={
                "status": "completed",
                "total_tokens": token_index,
                "latency_ms": total_duration_ms,
                "ttft_ms": ttft_ms or total_duration_ms,
            },
        )

    async def _persist_messages_and_audit(
        self,
        conn: AsyncConnection,
        scope: UserScope,
        conversation_id: UUID,
        message_id: UUID,
        query: str,
        assistant_content: str,
        citations: list[CitationPayload],
        refused: bool,
        latency_ms: int,
    ) -> None:
        """Appends user and assistant messages, updates conversation, and writes audit log."""
        # Get current maximum ordinal
        ord_query = text(
            "SELECT COALESCE(MAX(ordinal), 0) FROM messages WHERE conversation_id = :conv_id"
        )
        ord_res = await conn.execute(ord_query, {"conv_id": conversation_id})
        max_ordinal = int(ord_res.scalar() or 0)

        # Insert User Message
        user_msg_stmt = text(
            """
            INSERT INTO messages (id, org_id, conversation_id, ordinal, role, content, refused)
            VALUES (:id, :org_id, :conversation_id, :ordinal, 'user', :content, false)
            """
        )
        await conn.execute(
            user_msg_stmt,
            {
                "id": uuid4(),
                "org_id": scope.org_id,
                "conversation_id": conversation_id,
                "ordinal": max_ordinal + 1,
                "content": query,
            },
        )

        # Insert Assistant Message
        citations_json = json.dumps([c.model_dump(mode="json") for c in citations])
        asst_msg_stmt = text(
            """
            INSERT INTO messages (id, org_id, conversation_id, ordinal, role, content, citations, refused)
            VALUES (:id, :org_id, :conversation_id, :ordinal, 'assistant', :content, :citations::jsonb, :refused)
            """
        )
        await conn.execute(
            asst_msg_stmt,
            {
                "id": message_id,
                "org_id": scope.org_id,
                "conversation_id": conversation_id,
                "ordinal": max_ordinal + 2,
                "content": assistant_content,
                "citations": citations_json,
                "refused": refused,
            },
        )

        # Update Conversation timestamp
        await conn.execute(
            text("UPDATE conversations SET updated_at = now() WHERE id = :id"),
            {"id": conversation_id},
        )

        # Record append-only audit event
        from veydus.audit.service import record_audit_event

        await record_audit_event(
            conn=conn,
            org_id=scope.org_id,
            actor_user_id=scope.user_id,
            event_type="rag_query_completed",
            payload={
                "conversation_id": str(conversation_id),
                "message_id": str(message_id),
                "refused": refused,
                "citations_count": len(citations),
                "latency_ms": latency_ms,
            },
        )
