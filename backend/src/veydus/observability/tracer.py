# ─────────────────────────────────────────────────────────────────
# VEYDUS — OpenTelemetry Tracer Provider & Instrumentation
# ─────────────────────────────────────────────────────────────────
# What:  Initializes and configures the OpenTelemetry TracerProvider, span processors,
#        and exporters (in-memory for CI/testing, OTLP for Arize Phoenix / collectors).
# How:   - Sets up TracerProvider with standard Resource attributes (service.name="veydus-api").
#        - Provides get_tracer() returning the authoritative named Tracer instance.
#        - Provides setup_in_memory_tracer() for hermetic testing with InMemorySpanExporter.
#        - Manages trace context propagation across asynchronous pipeline stages.
# Why:   HLD §13.1 & §14 mandate exact request tracing across all 13 pipeline stages
#        to measure latency budgets and verify absence of sensitive data in spans.
# Tools: opentelemetry.trace, opentelemetry.sdk.trace, opentelemetry.sdk.trace.export.
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

if TYPE_CHECKING:
    from opentelemetry.trace import Tracer

logger = logging.getLogger(__name__)

_SERVICE_NAME = "veydus-api"
_in_memory_exporter: InMemorySpanExporter | None = None


def init_tracer(otlp_endpoint: str | None = None, debug_console: bool = False) -> TracerProvider:
    """Initializes and registers the global OpenTelemetry TracerProvider."""
    resource = Resource.create({"service.name": _SERVICE_NAME, "service.version": "0.1.0"})
    provider = TracerProvider(resource=resource)

    if otlp_endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

            otlp_exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
            provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
            logger.info("OTLP trace exporter connected to endpoint: %s", otlp_endpoint)
        except Exception as exc:
            logger.warning("Failed to initialize OTLP span exporter: %s", exc)

    if debug_console:
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))

    trace.set_tracer_provider(provider)
    return provider


def reset_tracer_provider() -> None:
    """Resets the global OpenTelemetry tracer provider state for testing isolation."""
    global _in_memory_exporter
    trace._TRACER_PROVIDER = None
    if hasattr(trace, "_TRACER_PROVIDER_SET_ONCE"):
        trace._TRACER_PROVIDER_SET_ONCE._done = False
    _in_memory_exporter = None


def setup_in_memory_tracer() -> tuple[TracerProvider, InMemorySpanExporter]:
    """Configures an isolated in-memory tracer provider for testing and validation."""
    global _in_memory_exporter
    reset_tracer_provider()
    resource = Resource.create({"service.name": f"{_SERVICE_NAME}-test"})
    provider = TracerProvider(resource=resource)
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    _in_memory_exporter = exporter
    return provider, exporter


def get_in_memory_exporter() -> InMemorySpanExporter | None:
    """Retrieves the active in-memory span exporter if configured."""
    return _in_memory_exporter


def get_tracer(name: str = "veydus") -> Tracer:
    """Returns a named OpenTelemetry tracer instance."""
    return trace.get_tracer(name)


def get_current_trace_id() -> str:
    """Retrieves the active 32-character hexadecimal OpenTelemetry trace ID."""
    span = trace.get_current_span()
    ctx = span.get_span_context()
    if ctx.is_valid:
        return f"{ctx.trace_id:032x}"
    return ""
