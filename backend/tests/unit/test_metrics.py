# ─────────────────────────────────────────────────────────────────
# VEYDUS — Unit Tests: OpenTelemetry Metrics & Security Counters
# ─────────────────────────────────────────────────────────────────
# What:  Unit tests verifying metric collection, security counter increments,
#        refusal tracking, grounding score histograms, and latency recording.
# How:   Calls record_* functions with synthetic attributes and verifies
#        instrument executions without raising exceptions.
# Why:   HLD §13.2 requirement: Verifies operational and security telemetry
#        recording contracts.
# Tools: pytest, uuid.uuid4, veydus.observability.metrics.
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

from uuid import uuid4

from veydus.observability.metrics import (
    record_access_filter_failure,
    record_nim_rate_limit,
    record_out_of_scope_detection,
    record_query_metrics,
)


def test_record_security_counters() -> None:
    org_id = uuid4()
    # Must execute cleanly without error
    record_out_of_scope_detection(org_id)
    record_access_filter_failure(org_id)
    record_nim_rate_limit(org_id)


def test_record_query_metrics_normal_and_refusal() -> None:
    org_id = uuid4()

    # Normal completed query
    record_query_metrics(
        org_id=org_id,
        refused=False,
        grounding_score=0.88,
        rows_returned=28,
        auth_and_retrieval_ms=142.5,
        ttft_ms=210.0,
        prompt_tokens=450,
        completion_tokens=85,
    )

    # Refused query (below threshold)
    record_query_metrics(
        org_id=org_id,
        refused=True,
        grounding_score=0.18,
        rows_returned=5,
        auth_and_retrieval_ms=95.0,
        ttft_ms=105.0,
    )
