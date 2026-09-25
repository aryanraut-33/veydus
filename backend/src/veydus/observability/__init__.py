# ─────────────────────────────────────────────────────────────────
# VEYDUS — Observability Package
# ─────────────────────────────────────────────────────────────────
# What:  Public exports for tracing, span definitions, structured logging,
#        and metrics collection.
# How:   Exposes canonical span names, safe span managers, and tracer accessors.
# Why:   HLD §13 requirement: Unified observability facade across the codebase.
# Tools: veydus.observability.tracer, veydus.observability.spans.
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

from veydus.observability.logging import (
    NeverLogFilter,
    clear_logging_context,
    configure_structured_logging,
    set_logging_context,
)
from veydus.observability.metrics import (
    record_access_filter_failure,
    record_nim_rate_limit,
    record_out_of_scope_detection,
    record_query_metrics,
)
from veydus.observability.spans import (
    ALL_REQUIRED_SPANS,
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
    safe_async_span,
    safe_span,
)
from veydus.observability.tracer import (
    get_current_trace_id,
    get_in_memory_exporter,
    get_tracer,
    init_tracer,
    reset_tracer_provider,
    setup_in_memory_tracer,
)

__all__ = [
    "ALL_REQUIRED_SPANS",
    "SPAN_AUTH_RESOLVE_SCOPE",
    "SPAN_AUTH_VERIFY_TOKEN",
    "SPAN_CONVERSATION_LOAD",
    "SPAN_GENERATION_BUILD_PROMPT",
    "SPAN_GENERATION_PII_REDACT",
    "SPAN_GENERATION_STREAM",
    "SPAN_QUERY_REWRITE",
    "SPAN_RETRIEVAL_EMBED_QUERY",
    "SPAN_RETRIEVAL_RERANK",
    "SPAN_RETRIEVAL_SESSION_SEARCH",
    "SPAN_RETRIEVAL_VECTOR_SEARCH",
    "SPAN_ROOT_QUERY",
    "SPAN_SECURITY_SCOPE_ASSERTION",
    "NeverLogFilter",
    "clear_logging_context",
    "configure_structured_logging",
    "get_current_trace_id",
    "get_in_memory_exporter",
    "get_tracer",
    "init_tracer",
    "record_access_filter_failure",
    "record_nim_rate_limit",
    "record_out_of_scope_detection",
    "record_query_metrics",
    "reset_tracer_provider",
    "safe_async_span",
    "safe_span",
    "set_logging_context",
    "setup_in_memory_tracer",
]
