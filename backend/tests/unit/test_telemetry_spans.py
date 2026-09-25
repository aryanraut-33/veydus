# ─────────────────────────────────────────────────────────────────
# VEYDUS — Unit Tests: OpenTelemetry Spans & Content Safety Invariant
# ─────────────────────────────────────────────────────────────────
# What:  Unit tests verifying OpenTelemetry span completeness across all 13 HLD §13.1
#        spans under veydus.query and asserting zero content in span attributes.
# How:   Configures an InMemorySpanExporter, simulates the complete span hierarchy,
#        inspects exported ReadableSpan objects, and walks every attribute dictionary.
# Why:   HLD §13.1 Success Check 1 & 2: Proves programmatic trace completeness and
#        absolute enforcement that chunk/query text is never stored in telemetry attributes.
# Tools: pytest, opentelemetry.sdk.trace, veydus.observability.
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

from uuid import uuid4

import pytest

from veydus.observability import (
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
    safe_span,
    setup_in_memory_tracer,
)


@pytest.fixture
def telemetry_exporter():
    provider, exporter = setup_in_memory_tracer()
    yield exporter
    exporter.clear()


def test_thirteen_spans_completeness(telemetry_exporter) -> None:
    org_id = uuid4()
    user_id = uuid4()

    # Simulate full trace lifecycle with all 13 spans
    with safe_span(SPAN_ROOT_QUERY, org_id=org_id, user_id=user_id):
        with safe_span(SPAN_AUTH_VERIFY_TOKEN, org_id=org_id, user_id=user_id):
            pass
        with safe_span(
            SPAN_AUTH_RESOLVE_SCOPE, org_id=org_id, user_id=user_id, attributes={"cache_hit": True}
        ):
            pass
        with safe_span(SPAN_CONVERSATION_LOAD, org_id=org_id, user_id=user_id):
            pass
        with safe_span(SPAN_QUERY_REWRITE, org_id=org_id, user_id=user_id):
            pass
        with safe_span(SPAN_RETRIEVAL_EMBED_QUERY, org_id=org_id, user_id=user_id):
            pass
        with safe_span(
            SPAN_RETRIEVAL_VECTOR_SEARCH,
            org_id=org_id,
            user_id=user_id,
            attributes={"k": 30, "ef_search": 100, "rows_returned": 25},
        ):
            pass
        with safe_span(SPAN_RETRIEVAL_SESSION_SEARCH, org_id=org_id, user_id=user_id):
            pass
        with safe_span(
            SPAN_RETRIEVAL_RERANK,
            org_id=org_id,
            user_id=user_id,
            attributes={"n_in": 25, "n_out": 6, "top_score": 0.88},
        ):
            pass
        with safe_span(
            SPAN_SECURITY_SCOPE_ASSERTION,
            org_id=org_id,
            user_id=user_id,
            attributes={"out_of_scope_count": 0},
        ):
            pass
        with safe_span(
            SPAN_GENERATION_PII_REDACT,
            org_id=org_id,
            user_id=user_id,
            attributes={"entities_found": ["EMAIL_ADDRESS"]},
        ):
            pass
        with safe_span(
            SPAN_GENERATION_BUILD_PROMPT,
            org_id=org_id,
            user_id=user_id,
            attributes={"prompt_tokens": 512},
        ):
            pass
        with safe_span(
            SPAN_GENERATION_STREAM,
            org_id=org_id,
            user_id=user_id,
            attributes={"ttft_ms": 45, "completion_tokens": 120},
        ):
            pass

    exported_spans = telemetry_exporter.get_finished_spans()
    exported_names = {s.name for s in exported_spans}

    # Success check 1: Exact match with all 13 canonical spans from HLD §13.1
    assert exported_names == ALL_REQUIRED_SPANS
    assert len(exported_spans) == 13

    # Root span check
    root_span = next(s for s in exported_spans if s.name == SPAN_ROOT_QUERY)
    assert root_span.parent is None


def test_zero_content_in_span_attributes(telemetry_exporter) -> None:
    canary_chunk = "CANARY_CONFIDENTIAL_CHUNK_SECRET_DATA_XYZ"
    canary_query = "CANARY_RAW_USER_QUERY_STRING_ABC"
    org_id = uuid4()
    user_id = uuid4()

    # Attempt to inject forbidden content attributes
    with (
        safe_span(
            SPAN_ROOT_QUERY,
            org_id=org_id,
            user_id=user_id,
            attributes={
                "query": canary_query,
                "chunk_content": canary_chunk,
                "legitimate_stat": 42,
            },
        ),
        safe_span(
            SPAN_RETRIEVAL_VECTOR_SEARCH,
            org_id=org_id,
            user_id=user_id,
            attributes={
                "k": 30,
                "prompt": f"System prompt containing {canary_chunk}",
                "user_query": canary_query,
            },
        ),
    ):
        pass

    exported_spans = telemetry_exporter.get_finished_spans()

    # Success check 2: Walk every exported span's attributes and assert no canary appears
    for span in exported_spans:
        attrs = span.attributes or {}
        # Ensure org_id and user_id are present
        assert attrs.get("org_id") == str(org_id)
        assert attrs.get("user_id") == str(user_id)

        for key, value in attrs.items():
            val_str = str(value)
            assert canary_chunk not in val_str, (
                f"Chunk canary leaked in attribute '{key}': {val_str}"
            )
            assert canary_query not in val_str, (
                f"Query canary leaked in attribute '{key}': {val_str}"
            )
            assert key not in {"query", "chunk_content", "prompt", "user_query"}
