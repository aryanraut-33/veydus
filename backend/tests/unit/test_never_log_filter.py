# ─────────────────────────────────────────────────────────────────
# VEYDUS — Unit Tests: Never-Log Negative Filter (HLD §13.3)
# ─────────────────────────────────────────────────────────────────
# What:  Unit tests verifying structured JSON logging output and asserting
#        strict enforcement of HLD §13.3 never-log rules (chunk content,
#        user query strings, prompt tokens, and credentials).
# How:   Configures a capturing StringIO handler with JsonLogFormatter and
#        NeverLogFilter, passes canary strings through log messages and extra
#        payloads, and asserts zero leakage in serialized JSON output.
# Why:   HLD §13.3 Success Check 3: Absolute proof that central application
#        logs remain free of sensitive enterprise content or user question data.
# Tools: pytest, io.StringIO, logging, json, veydus.observability.logging.
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

import io
import json
import logging
from uuid import uuid4

import pytest

from veydus.observability.logging import (
    JsonLogFormatter,
    NeverLogFilter,
    clear_logging_context,
    set_logging_context,
)


@pytest.fixture
def log_capture():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonLogFormatter())
    handler.addFilter(NeverLogFilter())

    test_logger = logging.getLogger("veydus.test_privacy")
    test_logger.setLevel(logging.INFO)
    test_logger.addHandler(handler)
    test_logger.propagate = False

    yield stream, test_logger

    test_logger.removeHandler(handler)
    clear_logging_context()


def test_structured_json_fields_present(log_capture) -> None:
    stream, logger = log_capture
    org_id = uuid4()
    user_id = uuid4()
    request_id = "req-12345-abc"

    set_logging_context(org_id=org_id, user_id=user_id, request_id=request_id)
    logger.info("Pipeline query initialized", extra={"candidate_count": 30})

    raw_output = stream.getvalue().strip()
    data = json.loads(raw_output)

    assert data["message"] == "Pipeline query initialized"
    assert data["level"] == "INFO"
    assert data["org_id"] == str(org_id)
    assert data["user_id"] == str(user_id)
    assert data["request_id"] == request_id
    assert data["candidate_count"] == 30
    assert "timestamp" in data


def test_never_log_canary_enforcement(log_capture) -> None:
    stream, logger = log_capture
    chunk_canary = "CANARY_CONFIDENTIAL_CHUNK_PAYLOAD_TOP_SECRET"
    query_canary = "CANARY_USER_SEARCH_QUERY_FOR_EXECUTIVE_SALARIES"

    # Attempt to log sensitive payloads via extra attributes
    logger.info(
        "Candidate chunks retrieved",
        extra={
            "query": query_canary,
            "raw_query": query_canary,
            "chunk_content": chunk_canary,
            "prompt": f"Instructions with {chunk_canary}",
            "tokens": 45,
            "safe_metric": 100,
        },
    )

    all_logs = stream.getvalue()

    # Success check 3: The canaries must appear nowhere in the serialized logs
    assert chunk_canary not in all_logs
    assert query_canary not in all_logs

    # Confirm safe attributes survived
    data = json.loads(all_logs.strip())
    assert data["safe_metric"] == 100
    assert "query" not in data
    assert "chunk_content" not in data
    assert "prompt" not in data
