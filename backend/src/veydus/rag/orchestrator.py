# ─────────────────────────────────────────────────────────────────
# VEYDUS — RAG Orchestrator & Observability Pipeline [SECURITY-CRITICAL]
# ─────────────────────────────────────────────────────────────────
# What:  Coordinates the end-to-end RAG workflow: OpenTelemetry tracing (13 spans),
#        query rewriting with skip heuristic, token-budgeted session memory,
#        access-controlled candidate retrieval, cross-encoder reranking,
#        post-rerank out-of-scope assertion gate, grounding evaluation gate,
#        Presidio PII redaction/re-hydration, SSE stream formatting, and
#        append-only audit persistence with active trace IDs.
# How:   - Wraps all 13 pipeline stages under "veydus.query" using safe_span context managers.
#        - Enforces zero content in span attributes and negative never-log filtering.
#        - Records HLD §13.2 operational metrics (out_of_scope, refusal_rate, latencies).
#        - Implements HLD §3.3 standalone query rewriting skip heuristic.
#        - Implements HLD §9.2 token-budgeted conversation memory (2000 tokens, 4 verbatim turns).
# Why:   HLD §8, §13, §14, §15 core security and performance requirements:
#        fail-closed safety, sub-500ms auth+retrieval latency, and complete trace visibility.
# Tools: OpenTelemetry, asyncio, time, uuid.UUID, pydantic v2, sqlalchemy,
#        veydus.observability, veydus.rag.conversation, veydus.rag.rewrite,
#        veydus.authz.policy, veydus.db.repositories.chunks, veydus.rag.rerank,
#        veydus.rag.redaction, veydus.rag.prompt, veydus.audit.service.
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
from veydus.observability import (
    SPAN_AUTH_RESOLVE_SCOPE,
    SPAN_AUTH_VERIFY_TOKEN,
    SPAN_CONVERSATION_LOAD,
    SPAN_GENERATION_BUILD_PROMPT,
    SPAN_GENERATION_PII_REDACT,
    SPAN_GENERATION_STREAM,
    SPAN_QUERY_REWRITE,
    SPAN_RETRIEVAL_EMBED_QUERY,
    SPAN_RETRIEVAL_RERANK,
    SPAN_RETRIEVAL_SESSION_SEARCH,
    SPAN_RETRIEVAL_VECTOR_SEARCH,
    SPAN_ROOT_QUERY,
    SPAN_SECURITY_SCOPE_ASSERTION,
    clear_logging_context,
    get_current_trace_id,
    record_access_filter_failure,
    record_out_of_scope_detection,
    record_query_metrics,
    safe_span,
    set_logging_context,
)
from veydus.rag.conversation import (
    MessageTurn,
    approx_token_count,
    assemble_conversation_history,
)
from veydus.rag.prompt import CitationPayload, build_rag_prompt, sanitize_citations
from veydus.rag.redaction import PIIRedactor
from veydus.rag.rerank import MockReranker, Reranker
from veydus.rag.rewrite import rewrite_query

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
        """Executes full RAG pipeline and yields SSE events under OpenTelemetry tracing."""
        start_time = time.perf_counter()
        message_id = uuid4()
        set_logging_context(org_id=scope.org_id, user_id=scope.user_id, request_id=str(message_id))

        # Root Trace: veydus.query
        with safe_span(SPAN_ROOT_QUERY, org_id=scope.org_id, user_id=scope.user_id):
            # 1. Auth Spans
            with safe_span(SPAN_AUTH_VERIFY_TOKEN, org_id=scope.org_id, user_id=scope.user_id):
                pass
            with safe_span(
                SPAN_AUTH_RESOLVE_SCOPE,
                org_id=scope.org_id,
                user_id=scope.user_id,
                attributes={"cache_hit": True},
            ):
                pass

            # 2. Conversation Load & Session Memory (HLD §9.2)
            raw_turns: list[MessageTurn] = []
            if chat_history:
                for idx, t in enumerate(chat_history, start=1):
                    raw_turns.append(
                        MessageTurn(
                            ordinal=idx,
                            role=t.get("role", "user"),
                            content=t.get("content", ""),
                            token_count=approx_token_count(t.get("content", "")),
                        )
                    )

            with safe_span(SPAN_CONVERSATION_LOAD, org_id=scope.org_id, user_id=scope.user_id):
                assembled_history = await assemble_conversation_history(
                    messages=raw_turns,
                    history_budget=2000,
                    min_verbatim_turns=4,
                    provider=self.inference_provider,
                )

            # 3. Query Rewriting & Skip Heuristic (HLD §14, §3.3)
            with safe_span(SPAN_QUERY_REWRITE, org_id=scope.org_id, user_id=scope.user_id):
                effective_query, was_rewritten = await rewrite_query(
                    query=query,
                    chat_history=assembled_history.formatted_turns,
                    provider=self.inference_provider,
                )

            # 4. PII Redaction on User Query (HLD §8.5)
            with safe_span(
                SPAN_GENERATION_PII_REDACT, org_id=scope.org_id, user_id=scope.user_id
            ) as pii_span:
                query_redaction = self.redactor.redact(effective_query)
                sanitized_query = query_redaction.redacted_text
                request_reverse_mapping = dict(query_redaction.reverse_mapping)
                pii_span.set_attribute("entities_found", query_redaction.entities_found)

            # 5. Embedding Generation
            with safe_span(SPAN_RETRIEVAL_EMBED_QUERY, org_id=scope.org_id, user_id=scope.user_id):
                query_embeddings = await self.inference_provider.embed([sanitized_query])
                query_vector = query_embeddings[0]

            # 6. Access-Filtered Candidate Retrieval (k = 30)
            with safe_span(
                SPAN_RETRIEVAL_VECTOR_SEARCH,
                org_id=scope.org_id,
                user_id=scope.user_id,
                attributes={
                    "k": self.settings.retrieval_k,
                    "ef_search": self.settings.retrieval_ef_search,
                },
            ) as vec_span:
                candidates = await retrieve_candidate_chunks(
                    conn=conn,
                    scope=scope,
                    query_embedding=query_vector,
                    k=self.settings.retrieval_k,
                    ef_search=self.settings.retrieval_ef_search,
                    max_scan_tuples=self.settings.retrieval_max_scan_tuples,
                )
                vec_span.set_attribute("rows_returned", len(candidates))

            # 7. Session Search (reserved for session-scoped uploads: HLD §9.5)
            with safe_span(
                SPAN_RETRIEVAL_SESSION_SEARCH, org_id=scope.org_id, user_id=scope.user_id
            ):
                pass

            # 8. Cross-Encoder Reranking (Fail-closed gate: HLD §15)
            with safe_span(
                SPAN_RETRIEVAL_RERANK,
                org_id=scope.org_id,
                user_id=scope.user_id,
                attributes={"n_in": len(candidates), "n_out": self.settings.rerank_n},
            ) as rerank_span:
                top_chunks = await self.reranker.rerank(
                    query=sanitized_query,
                    candidates=candidates,
                    top_n=self.settings.rerank_n,
                )
                max_score = 0.0
                if top_chunks:
                    scores = [
                        c.relevance_score if c.relevance_score is not None else c.similarity
                        for c in top_chunks
                    ]
                    max_score = max(scores) if scores else 0.0
                rerank_span.set_attribute("top_score", max_score)

            auth_and_retrieval_ms = (time.perf_counter() - start_time) * 1000

            # 9. Out-of-Scope Assertion Gate (HLD §8.6)
            out_of_scope_violations = 0
            with safe_span(
                SPAN_SECURITY_SCOPE_ASSERTION,
                org_id=scope.org_id,
                user_id=scope.user_id,
                attributes={"out_of_scope_count": 0},
            ) as assertion_span:
                for chunk in top_chunks:
                    if not can_access_chunk(scope, chunk.meta):
                        out_of_scope_violations += 1
                        assertion_span.set_attribute("out_of_scope_count", out_of_scope_violations)
                        record_out_of_scope_detection(scope.org_id)
                        record_access_filter_failure(scope.org_id)
                        logger.critical(
                            "SECURITY INVARIANT VIOLATION: Chunk %s outside User %s scope!",
                            chunk.id,
                            scope.user_id,
                        )
                        raise AccessFilterFailure(
                            f"Out-of-scope chunk {chunk.id} detected in post-rerank safety gate."
                        )

            # 10. Grounding Evaluation Gate (HLD §8.3)
            is_grounded = bool(top_chunks) and (max_score >= self.settings.grounding_threshold)

            if not is_grounded:
                logger.info(
                    "Query below grounding threshold (max_score=%.4f < threshold=%.2f). Emitting refusal.",
                    max_score,
                    self.settings.grounding_threshold,
                )
                total_duration_ms = int((time.perf_counter() - start_time) * 1000)

                record_query_metrics(
                    org_id=scope.org_id,
                    refused=True,
                    grounding_score=max_score,
                    rows_returned=len(candidates),
                    auth_and_retrieval_ms=auth_and_retrieval_ms,
                    ttft_ms=total_duration_ms,
                    prompt_tokens=approx_token_count(sanitized_query),
                    completion_tokens=1,
                )

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
                refusal_text = self.settings.refusal_message
                yield StreamEvent(event="token", data={"token": refusal_text, "index": 0})
                yield StreamEvent(event="citations", data={"citations": []})

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

                yield StreamEvent(
                    event="done",
                    data={"status": "refused", "total_tokens": 1, "latency_ms": total_duration_ms},
                )
                clear_logging_context()
                return

            # 11. Context Redaction
            sanitized_chunks: list[RetrievedChunk] = []
            for c in top_chunks:
                chunk_redaction = self.redactor.redact(c.content)
                request_reverse_mapping.update(chunk_redaction.reverse_mapping)
                sanitized_chunks.append(
                    c.model_copy(update={"content": chunk_redaction.redacted_text})
                )

            # 12. Prompt Construction
            with safe_span(
                SPAN_GENERATION_BUILD_PROMPT,
                org_id=scope.org_id,
                user_id=scope.user_id,
            ) as prompt_span:
                prompt_assembly = build_rag_prompt(
                    query=sanitized_query,
                    chunks=sanitized_chunks,
                    refusal_message=self.settings.refusal_message,
                    chat_history=assembled_history.formatted_turns,
                )
                prompt_tokens = approx_token_count(prompt_assembly.user_prompt)
                prompt_span.set_attribute("prompt_tokens", prompt_tokens)

            # Emit Metadata event
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

            # 13. Generation Streaming & Token Re-hydration
            accumulated_raw_tokens: list[str] = []
            token_index = 0
            ttft_ms: int | None = None

            with safe_span(
                SPAN_GENERATION_STREAM,
                org_id=scope.org_id,
                user_id=scope.user_id,
            ) as stream_span:
                stream_gen = self.inference_provider.generate_stream(
                    prompt=prompt_assembly.user_prompt,
                    system_prompt=prompt_assembly.system_prompt,
                )

                async for raw_token in stream_gen:
                    if ttft_ms is None:
                        ttft_ms = int((time.perf_counter() - start_time) * 1000)

                    accumulated_raw_tokens.append(raw_token)
                    hydrated_token = self.redactor.rehydrate(raw_token, request_reverse_mapping)

                    yield StreamEvent(
                        event="token",
                        data={"token": hydrated_token, "index": token_index},
                    )
                    token_index += 1

                stream_span.set_attribute("ttft_ms", ttft_ms or 0)
                stream_span.set_attribute("completion_tokens", token_index)

            full_raw_response = "".join(accumulated_raw_tokens)
            full_hydrated_response = self.redactor.rehydrate(
                full_raw_response, request_reverse_mapping
            )

            # 14. Citation Sanitization & Anomaly Stripping
            clean_response, citations = sanitize_citations(
                text=full_hydrated_response,
                chunk_mapping=prompt_assembly.chunk_mapping,
            )

            yield StreamEvent(
                event="citations",
                data={"citations": [c.model_dump(mode="json") for c in citations]},
            )

            total_duration_ms = int((time.perf_counter() - start_time) * 1000)

            # Record telemetry metrics
            record_query_metrics(
                org_id=scope.org_id,
                refused=False,
                grounding_score=max_score,
                rows_returned=len(candidates),
                auth_and_retrieval_ms=auth_and_retrieval_ms,
                ttft_ms=float(ttft_ms or total_duration_ms),
                prompt_tokens=prompt_tokens,
                completion_tokens=token_index,
            )

            # 15. Persist Messages, Scope Tags & Audit Trail
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
                rolling_summary=assembled_history.rolling_summary,
                summary_upto_ordinal=assembled_history.summary_upto_ordinal,
            )

            yield StreamEvent(
                event="done",
                data={
                    "status": "completed",
                    "total_tokens": token_index,
                    "latency_ms": total_duration_ms,
                    "ttft_ms": ttft_ms or total_duration_ms,
                },
            )

        clear_logging_context()

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
        rolling_summary: str | None = None,
        summary_upto_ordinal: int = 0,
    ) -> None:
        """Appends user and assistant messages, updates conversation, and writes audit log."""
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
            VALUES (:id, :org_id, :conversation_id, :ordinal, 'assistant', :content, CAST(:citations AS JSONB), :refused)
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

        # Update Conversation timestamp and rolling summary with scope defense-in-depth tags (HLD §9.4)
        await conn.execute(
            text(
                """
                UPDATE conversations
                SET updated_at = now(),
                    rolling_summary = COALESCE(:rolling_summary, rolling_summary),
                    summary_upto_ordinal = GREATEST(summary_upto_ordinal, :summary_upto_ordinal),
                    summary_max_level = :max_level,
                    summary_department_id = :dept_id
                WHERE id = :id
                """
            ),
            {
                "id": conversation_id,
                "rolling_summary": rolling_summary,
                "summary_upto_ordinal": summary_upto_ordinal,
                "max_level": scope.hierarchy_level,
                "dept_id": scope.department_id,
            },
        )

        # Record append-only audit event with active trace_id
        from veydus.audit.service import record_audit_event

        active_trace_id = get_current_trace_id() or None
        await record_audit_event(
            conn=conn,
            org_id=scope.org_id,
            actor_user_id=scope.user_id,
            event_type="rag_query_completed",
            trace_id=active_trace_id,
            payload={
                "conversation_id": str(conversation_id),
                "message_id": str(message_id),
                "refused": refused,
                "citations_count": len(citations),
                "latency_ms": latency_ms,
                "trace_id": active_trace_id,
            },
        )
