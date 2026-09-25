# ─────────────────────────────────────────────────────────────────
# VEYDUS — Span Definitions & Safe Telemetry Context [SECURITY-CRITICAL]
# ─────────────────────────────────────────────────────────────────
# What:  Defines the 13 canonical OpenTelemetry span names specified in HLD §13.1
#        and provides context managers for span lifecycle management with strict
#        attribute validation.
# How:   - Pinned string constants for all 13 spans under the root "veydus.query".
#        - safe_span(): Context manager that attaches mandatory tenant attributes
#          (org_id, user_id) and enforces the negative attribute filter.
#        - Rejects any attribute key or value attempting to store chunk text,
#          raw query strings, or sensitive payloads (HLD §13.1 security invariant).
# Why:   HLD §13.1 mandate: Every request stage must be traceable and measurable
#        without leaking plaintext document chunks or queries into telemetry collectors.
# Tools: opentelemetry.trace, contextlib.asynccontextmanager, contextlib.contextmanager,
#        veydus.observability.tracer.
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

import logging
from contextlib import asynccontextmanager, contextmanager
from typing import TYPE_CHECKING, Any
from uuid import UUID

from opentelemetry.trace import Status, StatusCode

from veydus.observability.tracer import get_tracer

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from opentelemetry.trace import Span

logger = logging.getLogger(__name__)

# ── HLD §13.1 Canonical Span Names ──────────────────────────────
SPAN_ROOT_QUERY = "veydus.query"
SPAN_AUTH_VERIFY_TOKEN = "auth.verify_token"
SPAN_AUTH_RESOLVE_SCOPE = "auth.resolve_scope"
SPAN_CONVERSATION_LOAD = "conversation.load"
SPAN_QUERY_REWRITE = "query.rewrite"
SPAN_RETRIEVAL_EMBED_QUERY = "retrieval.embed_query"
SPAN_RETRIEVAL_VECTOR_SEARCH = "retrieval.vector_search"
SPAN_RETRIEVAL_SESSION_SEARCH = "retrieval.session_search"
SPAN_RETRIEVAL_RERANK = "retrieval.rerank"
SPAN_SECURITY_SCOPE_ASSERTION = "security.scope_assertion"
SPAN_GENERATION_PII_REDACT = "generation.pii_redact"
SPAN_GENERATION_BUILD_PROMPT = "generation.build_prompt"
SPAN_GENERATION_STREAM = "generation.stream"

ALL_REQUIRED_SPANS: frozenset[str] = frozenset(
    {
        SPAN_ROOT_QUERY,
        SPAN_AUTH_VERIFY_TOKEN,
        SPAN_AUTH_RESOLVE_SCOPE,
        SPAN_CONVERSATION_LOAD,
        SPAN_QUERY_REWRITE,
        SPAN_RETRIEVAL_EMBED_QUERY,
        SPAN_RETRIEVAL_VECTOR_SEARCH,
        SPAN_RETRIEVAL_SESSION_SEARCH,
        SPAN_RETRIEVAL_RERANK,
        SPAN_SECURITY_SCOPE_ASSERTION,
        SPAN_GENERATION_PII_REDACT,
        SPAN_GENERATION_BUILD_PROMPT,
        SPAN_GENERATION_STREAM,
    }
)

FORBIDDEN_ATTRIBUTE_KEYS: frozenset[str] = frozenset(
    {
        "content",
        "chunk_content",
        "query",
        "query_text",
        "raw_query",
        "user_query",
        "prompt",
        "system_prompt",
        "user_prompt",
        "embedding",
        "vector",
        "token",
        "password",
        "secret",
        "key",
    }
)


def _sanitize_attributes(
    org_id: UUID | str | None,
    user_id: UUID | str | None,
    attributes: dict[str, Any] | None,
) -> dict[str, Any]:
    """Validates and attaches safe attributes, scrubbing forbidden content keys."""
    sanitized: dict[str, Any] = {}

    if org_id is not None:
        sanitized["org_id"] = str(org_id)
    if user_id is not None:
        sanitized["user_id"] = str(user_id)

    if attributes:
        for k, v in attributes.items():
            k_lower = k.lower()
            if any(forbidden in k_lower for forbidden in FORBIDDEN_ATTRIBUTE_KEYS):
                logger.warning(
                    "SECURITY ATTEMPT BLOCKED: Forbidden attribute key '%s' omitted from span",
                    k,
                )
                continue
            if isinstance(v, (int, float, bool)):
                sanitized[k] = v
            elif isinstance(v, (str, UUID)):
                val_str = str(v)
                if len(val_str) > 256:
                    logger.warning(
                        "SECURITY ATTEMPT BLOCKED: Long attribute value for '%s' truncated/omitted",
                        k,
                    )
                    continue
                sanitized[k] = val_str
            elif isinstance(v, (list, tuple)):
                sanitized[k] = [str(item) for item in v[:50]]

    return sanitized


@contextmanager
def safe_span(
    name: str,
    org_id: UUID | str | None = None,
    user_id: UUID | str | None = None,
    attributes: dict[str, Any] | None = None,
) -> Iterator[Span]:
    """Synchronous context manager for an OpenTelemetry span with safety enforcement."""
    tracer = get_tracer()
    safe_attrs = _sanitize_attributes(org_id, user_id, attributes)

    with tracer.start_as_current_span(name, attributes=safe_attrs) as span:
        try:
            yield span
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            raise


@asynccontextmanager
async def safe_async_span(
    name: str,
    org_id: UUID | str | None = None,
    user_id: UUID | str | None = None,
    attributes: dict[str, Any] | None = None,
) -> AsyncIterator[Span]:
    """Asynchronous context manager for an OpenTelemetry span with safety enforcement."""
    tracer = get_tracer()
    safe_attrs = _sanitize_attributes(org_id, user_id, attributes)

    with tracer.start_as_current_span(name, attributes=safe_attrs) as span:
        try:
            yield span
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            raise
