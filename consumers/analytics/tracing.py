"""Cross-process tracing — Week 10.

Mirrors gateway/app/tracing.py's setup, but this process's spans are
children of a *different* process's trace: the gateway injects a W3C
traceparent into each event at emit time (app/events.py, app/tracing.py's
current_traceparent()); this consumer extracts it per-event and starts a
span parented to that extracted context, so a single Jaeger trace shows a
gateway request and its downstream analytics processing as one continuous
timeline, not two disconnected traces.
"""

import os

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.propagate import extract
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

_provider: TracerProvider | None = None


def setup_tracing() -> None:
    global _provider
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://jaeger:4317")
    resource = Resource(attributes={SERVICE_NAME: "shieldstream.analytics-consumer"})
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    _provider = provider


def shutdown_tracing() -> None:
    if _provider is not None:
        _provider.shutdown()


tracer = trace.get_tracer("shieldstream.analytics-consumer")


def span_for_event(traceparent: str):
    """Returns a context manager: a span parented to the gateway's original
    trace if traceparent is present and valid, otherwise a normal
    standalone span (never raises or skips tracing just because one event
    predates this feature or the field is empty)."""
    ctx = extract({"traceparent": traceparent}) if traceparent else None
    return tracer.start_as_current_span("analytics.ingest_event", context=ctx)
