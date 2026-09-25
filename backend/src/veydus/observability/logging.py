# ─────────────────────────────────────────────────────────────────
# VEYDUS — Structured JSON Logging & Negative Privacy Filter [SECURITY-CRITICAL]
# ─────────────────────────────────────────────────────────────────
# What:  Cloud Logging-compatible structured JSON logging formatter, contextual
#        logging variables, and a strict negative filter enforcing HLD §13.3.
# How:   - Formats log records into JSON dictionaries with standard keys:
#          timestamp, level, message, logger, trace_id, org_id, user_id, request_id.
#        - Uses Python contextvars for request-scoped correlation identifiers.
#        - NeverLogFilter: Enforces HLD §13.3 never-log list (chunk content,
#          message content, query text, embeddings, tokens, secrets), scrubbing
#          or blocking sensitive extra attributes.
# Why:   HLD §13.3 mandate: Guarantee that plaintext user queries, prompt tokens,
#        and enterprise document chunks are never leaked into application log streams.
# Tools: logging, json, datetime, contextvars, veydus.observability.tracer.
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

import contextvars
import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from veydus.observability.tracer import get_current_trace_id

if TYPE_CHECKING:
    from uuid import UUID

# ── Context Variables for Request-Scoped Logging ─────────────────
ctx_org_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("ctx_org_id", default=None)
ctx_user_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "ctx_user_id", default=None
)
ctx_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "ctx_request_id", default=None
)


def set_logging_context(
    org_id: UUID | str | None = None,
    user_id: UUID | str | None = None,
    request_id: str | None = None,
) -> None:
    """Sets contextual logging attributes for the current request execution."""
    if org_id is not None:
        ctx_org_id.set(str(org_id))
    if user_id is not None:
        ctx_user_id.set(str(user_id))
    if request_id is not None:
        ctx_request_id.set(request_id)


def clear_logging_context() -> None:
    """Clears contextual logging attributes."""
    ctx_org_id.set(None)
    ctx_user_id.set(None)
    ctx_request_id.set(None)


# ── HLD §13.3 Never-Log Negative Filter ──────────────────────────
# "Never logged: chunk content, message content, query text, embeddings, tokens, secrets."
NEVER_LOG_KEYS: frozenset[str] = frozenset(
    {
        "content",
        "chunk_content",
        "chunk_text",
        "query",
        "query_text",
        "raw_query",
        "user_query",
        "message_content",
        "embedding",
        "vector",
        "tokens",
        "prompt",
        "secret",
        "password",
        "api_key",
        "jwt_secret",
    }
)


class NeverLogFilter(logging.Filter):
    """Enforces HLD §13.3 by stripping sensitive keys from log record payloads."""

    def filter(self, record: logging.LogRecord) -> bool:
        # Scrub sensitive attributes attached via logger.info("...", extra={...})
        record_dict = record.__dict__
        for key in list(record_dict.keys()):
            key_lower = key.lower()
            if any(forbidden in key_lower for forbidden in NEVER_LOG_KEYS):
                record_dict.pop(key, None)

        return True


class JsonLogFormatter(logging.Formatter):
    """Formats log records as single-line JSON objects for Cloud Logging."""

    def format(self, record: logging.LogRecord) -> str:
        trace_id = get_current_trace_id()
        org_id = ctx_org_id.get()
        user_id = ctx_user_id.get()
        request_id = ctx_request_id.get()

        log_data: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "trace_id": trace_id,
            "org_id": org_id,
            "user_id": user_id,
            "request_id": request_id,
        }

        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        # Include safe custom extra fields
        standard_attrs = {
            "name",
            "msg",
            "args",
            "levelname",
            "levelno",
            "pathname",
            "filename",
            "module",
            "exc_info",
            "exc_text",
            "stack_info",
            "lineno",
            "funcName",
            "created",
            "msecs",
            "relativeCreated",
            "thread",
            "threadName",
            "processName",
            "process",
            "message",
        }
        for k, v in record.__dict__.items():
            if k not in standard_attrs and not k.startswith("_") and k not in log_data:
                k_lower = k.lower()
                if not any(forbidden in k_lower for forbidden in NEVER_LOG_KEYS) and isinstance(
                    v, (int, float, bool, str, list, dict)
                ):
                    log_data[k] = v

        return json.dumps(log_data)


def configure_structured_logging(log_level: str = "INFO") -> None:
    """Configures the root logging handler with JsonLogFormatter and NeverLogFilter."""
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level.upper())

    # Remove existing stream handlers
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)

    handler = logging.StreamHandler()
    handler.setFormatter(JsonLogFormatter())
    handler.addFilter(NeverLogFilter())
    root_logger.addHandler(handler)
